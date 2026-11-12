# Streamlit Dashboard Prototype Plan

## Goal

Build a prototype dashboard that primarily provides read-only access to the
city database. Selecting a city should show its detailed data and display the
associated zoning geometries on a map.

The database sources may contain ArcGIS endpoints, GeoJSON, Shapefiles, or
KMZ files. A nightly validation process should check the sources and expose
issues as dashboard notifications.

## Prototype Architecture

### Dashboard

Continue using Streamlit for the prototype. The dashboard should provide:

- Search and filtering by city, county, and state
- A table containing all cities
- Status indicators for the latest validation, source date, and open issues
- A city detail view opened by selecting a table entry
- Read-only display of imported city metadata
- Source URLs and validation history
- Notifications and alert events
- A map showing the selected city's geometry or geometries

Streamlit is appropriate while the prototype has a small number of users and
does not require highly granular map interactions.

### Current implementation status

The current Streamlit app implements the city table, city detail view,
imported metadata, source records, validation history, notifications, and
live ArcGIS layer rendering. The map currently loads ArcGIS layers directly
when a city is selected; it does not yet implement the planned cached,
format-independent geometry pipeline.

### Data layer

Continue using SQLite for the prototype. The existing schema already supports:

- `locations`
- `sources`
- `poll_runs`
- `observations`
- `alert_events`

The existing raw imported fields should remain available through
`locations.raw_metadata`.

### Geometry storage and processing

Implement geometry handling separately from date validation:

1. Detect the source format.
2. Download the source.
3. Convert GeoJSON, Shapefile, and KMZ data into a common GeoJSON form.
4. Cache the converted result.
5. Render the cached GeoJSON on the city detail map.

Add a separate `geometries` table rather than storing geometry directly on
`locations`:

```text
geometries
----------
id
location_id
source_url
format
geojson_path
retrieved_at
checksum
status
error
```

For the prototype, storing converted GeoJSON as files is simpler than storing
large geometry blobs in SQLite. Use PyDeck or Folium for rendering; prefer
PyDeck if geometry size becomes large.

### Nightly validation

Run polling as a separate process, not inside the Streamlit server. Add a
standalone entry point such as:

```powershell
python -m app.nightly_poll
```

The nightly process should:

- Import or refresh source data.
- Run the existing database poller.
- Store observations and alert events.
- Record a completed or failed poll run.
- Return a non-zero exit code when the run fails.

Schedule this command with Windows Task Scheduler. Streamlit should read and
display the resulting records.

## Recommended Implementation Order

1. ~~Add database query helpers for listing cities and loading city details.~~
2. ~~Replace the current poller-only page with a city database table.~~
3. ~~Add the city detail view and validation history.~~
4. Add geometry retrieval, caching, and format conversion.
5. ~~Add map rendering for the selected city.~~
6. Add the standalone nightly poll command.
7. Add notification filtering and acknowledgement behavior.
8. Add focused tests for database queries, geometry conversion, and poll
   scheduling behavior.

## Scope Boundaries

Do not migrate to Dash or a separate frontend yet. The current prototype is
ready for evaluating alternatives, but migration should wait until the project
requires many concurrent users, permissions, large geometry datasets, or
frequent interactive map filtering. Those requirements should be demonstrated
with expected usage and performance needs rather than treated as a technology
upgrade by itself.

Do not run APScheduler inside the Streamlit process. The scheduler and
dashboard should remain independently restartable.

PostgreSQL/PostGIS can replace SQLite later if spatial queries, concurrent
writes, or larger datasets require it. Any frontend migration should keep the
polling process independent from the web application and preserve the existing
monitoring records and alert behavior.
