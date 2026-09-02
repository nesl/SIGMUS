# Multi-source Q/A checks

The fixture describes one synthetic warehouse fire corroborated by camera,
Citizen, air-quality, and weather data. It contains no production observations.

Expected tool paths:

1. **Was a fire corroborated by multiple sources near downtown Los Angeles?**
   Use `search_reports`, `search_incidents`, and `find_related_reports`. Cite
   `qa-alert-fire-20260902`, `qa-cctv-fire-20260902`, and
   `qa-citizen-fire-20260902`.
2. **How did PM2.5 change between 17:00 and 20:00 UTC?**
   Use `query_measurements` or `aggregate_measurements`. The fixture rises from
   18.2 to 86.7 µg/m³ on `purpleair-1001`.
3. **What conditions might have affected smoke movement?**
   Combine graph evidence with weather measurements. Wind is 6.8 with direction
   245 degrees at 19:10 UTC; avoid claiming causality.
4. **Was flooding reported?**
   Return that the fixture contains no supporting report rather than inventing
   one.
