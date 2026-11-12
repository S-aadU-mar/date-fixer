from pathlib import Path
import sqlite3

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

from monitoring import (
    DEFAULT_DB_PATH,
    count_open_alerts,
    get_location_details,
    import_csv_files,
    load_arcgis_layers,
    list_alerts,
    list_locations,
    poll_database,
)


st.set_page_config(
    page_title="Zoning Source Poller",
    page_icon="✓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 2.5rem; padding-bottom: 3rem; }
    .hero { padding: 1.4rem 1.6rem; border-radius: 18px; color: white;
            background: linear-gradient(120deg, #2534B0, #126E82); margin-bottom: 1.5rem; }
    .hero { position: relative; }
    .hero h1 { margin: 0; font-size: 2.25rem; }
    .hero p { margin: .35rem 0 0; opacity: .85; }
    .notification-badge { display: inline-block; margin-top: .8rem; padding: .3rem .7rem;
                          border-radius: 999px; background: rgba(0, 0, 0, .22);
                          font-size: .9rem; }
    .city-details { border-bottom: 1px solid rgba(128, 128, 128, .35);
                    margin-bottom: 1rem; padding-bottom: .35rem; }
    [data-testid="stMetric"] {
        background: rgba(255, 255, 255, .08);
        border: 1px solid rgba(255, 255, 255, .18);
        padding: .8rem;
        border-radius: 12px;
    }
    [data-testid="stMetricLabel"] { color: inherit !important; }
    [data-testid="stMetricValue"] { color: inherit !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

open_alerts = count_open_alerts()
notification_label = (
    f"🔔 {open_alerts} issue(s) need attention"
    if open_alerts
    else "🔔 No open issues"
)
st.markdown(
    '<div class="hero"><h1>Zoning database</h1>'
    "<p>Browse city source records and review validation issues from the latest poll.</p>"
    f'<div class="notification-badge">{notification_label}</div></div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header(f"Notifications ({open_alerts})")
    alerts = list_alerts()
    if "type" not in alerts.columns:
        if "event_type" in alerts.columns:
            alerts = alerts.rename(columns={"event_type": "type"})
        else:
            alerts["type"] = alerts.apply(
                lambda row: "updated"
                if str(row.get("message", "")).startswith("Attention!")
                else "issue",
                axis=1,
            )
    alerts = alerts.rename(columns={"name": "city"})
    alerts = alerts.drop(columns=["acknowledged_at"], errors="ignore")
    if not alerts.empty:
        notification_view = st.radio(
            "View",
            ("Issues", "Updates", "All"),
            horizontal=True,
            label_visibility="collapsed",
        )
        if notification_view == "Issues":
            alerts = alerts[alerts["type"].eq("issue")]
        elif notification_view == "Updates":
            alerts = alerts[alerts["type"].eq("updated")]
        st.caption(f"{len(alerts)} notification(s)")
        st.dataframe(
            alerts,
            use_container_width=True,
            hide_index=True,
            column_config={
                "created_at": st.column_config.DatetimeColumn("When", format="YYYY-MM-DD HH:mm"),
                "city": "City",
                "county": "County",
                "state": "State",
                "type": "Type",
                "message": "Details",
            },
        )
    else:
        st.success("No open validation issues.")
    st.divider()
    st.header("Poller")
    st.caption("Run a validation poll without replacing the city catalog.")
    run_poller = st.button("Run poller now", type="primary", use_container_width=True)

if run_poller:
    source_files = sorted(Path(__file__).resolve().parents[1].joinpath("data").glob("*.csv"))
    try:
        if not DEFAULT_DB_PATH.exists():
            import_csv_files(source_files)
        progress_bar = st.progress(0, text="Starting poller...")
        poll_status = st.empty()

        def update_poll_progress(current: int, total: int, message: str) -> None:
            fraction = current / total if total else 0
            progress_bar.progress(min(fraction, 1.0), text=message)
            poll_status.caption(message)

        result = poll_database(progress=update_poll_progress)
        progress_bar.progress(1.0, text="Poller complete")
        poll_status.success("Poller finished successfully.")
        st.sidebar.success(
            f"Checked {result['checked']:,}; updated {result['updated']:,}; "
            f"issues {result['review']:,}. Refresh to update notifications."
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        st.sidebar.error(f"Poll failed: {type(exc).__name__}: {exc}")

st.subheader("City sources")
st.caption("Select a city row to expand its imported attributes and validation history.")
locations = list_locations()
if locations.empty:
    st.info("No cities are imported yet. Run the poller after adding source CSV files.")
else:
    detail_container = st.container()
    display_locations = locations.drop(columns=["location_id"])
    selected = st.dataframe(
        display_locations,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="city_catalog",
    )
    selected_rows = selected.selection.rows
    if selected_rows:
        location_id = int(locations.iloc[selected_rows[0]]["location_id"])
        details = get_location_details(location_id)
        if details:
            location = details["location"]
            with detail_container:
                st.markdown(
                    f'<div class="city-details"><h3>{location["name"]}, '
                    f'{location["state"]}</h3></div>',
                    unsafe_allow_html=True,
                )
                metadata = details["metadata"]
                detail_columns = st.columns(2)
                detail_columns[0].write(f"**County:** {location['county'] or '—'}")
                detail_columns[1].write(f"**Source file:** {location['source_file'] or '—'}")
                with st.container(border=True):
                    st.subheader("Map")
                    city_map = folium.Map(
                        location=[39.8283, -98.5795],
                        zoom_start=4,
                        tiles="OpenStreetMap",
                    )
                    arcgis_urls = tuple(
                        source["url"]
                        for source in details["sources"].to_dict("records")
                        if source["kind"] == "arcgis"
                    )
                    if arcgis_urls:
                        try:
                            loaded_layers, layer_errors = load_arcgis_layers(
                                arcgis_urls[0]
                            )
                            all_bounds = []
                            for layer_url, layer_geojson in loaded_layers:
                                features = layer_geojson.get("features", [])
                                properties = (
                                    features[0].get("properties", {})
                                    if features and isinstance(features[0], dict)
                                    else {}
                                )
                                fields = list(properties)[:8]
                                layer_name = layer_url.rsplit("/", 1)[-1]
                                geojson_layer = folium.GeoJson(
                                    layer_geojson,
                                    name=f"Layer {layer_name}",
                                    tooltip=(
                                        folium.GeoJsonTooltip(fields=fields)
                                        if fields
                                        else None
                                    ),
                                    popup=(
                                        folium.GeoJsonPopup(fields=fields)
                                        if fields
                                        else None
                                    ),
                                ).add_to(city_map)
                                all_bounds.append(geojson_layer.get_bounds())
                            if all_bounds:
                                points = [
                                    point
                                    for bounds in all_bounds
                                    for point in bounds
                                    if all(value is not None for value in point)
                                ]
                                if points:
                                    city_map.fit_bounds(
                                        [
                                            [min(point[0] for point in points), min(point[1] for point in points)],
                                            [max(point[0] for point in points), max(point[1] for point in points)],
                                        ]
                                    )
                            folium.LayerControl().add_to(city_map)
                            st.caption(f"Loaded {len(loaded_layers)} zoning layer(s).")
                            if layer_errors:
                                st.warning(
                                    f"{len(layer_errors)} layer(s) could not be loaded."
                                )
                        except (OSError, requests.RequestException, ValueError) as exc:
                            st.error(f"Could not load zoning layers: {exc}")
                    else:
                        st.info("No ArcGIS endpoint is available for this city.")
                    st_folium(
                        city_map,
                        use_container_width=True,
                        height=500,
                        key=f"city-map-{location_id}",
                    )
                with st.expander("Imported attributes", expanded=True):
                    st.json(metadata)
                st.markdown("#### Sources")
                st.dataframe(details["sources"], use_container_width=True, hide_index=True)
                st.markdown("#### Validation history")
                st.dataframe(details["observations"], use_container_width=True, hide_index=True)
