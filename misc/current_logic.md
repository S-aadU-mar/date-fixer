When it checks an endpoint, it stops at the first step that finds a date:

1. The **EditDate field**. The script finds the layer’s edit-date field, using the layer’s editFieldsInfo or a date field named EditDate, LastEditDate or similar. It asks the server for the newest value in that field, first with a max statistic and then by sorting descending and taking the top row, in case the server doesn’t support statistics.
2. The **Last Edit Date values**. It uses Data Last Edit Date, and falls back to Last Edit Date. These are the values shown under “Last Edit Date” on the page, from the layer’s editingInfo. I ignore Schema Last Edit Date, because a schema change isn’t a data update. Tell me if you want it included.
3. The **portal item’s modified date**, for hosted layers.
4. The **metadata ModDate**, as added earlier.
5. ~~The **viewer link**, only if all of the above fail.~~
