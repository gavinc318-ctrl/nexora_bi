# Information Request — GIS Integration (HudHud)

META|Project|OSS911 Makkah 911 Operations Centre — Business Intelligence and Data Warehouse subsystem
META|From|Nexora-BI
META|Date|7 October 2026
META|Requested reply by|[date]

## Purpose

We are asking for **two things**: the interface documentation for your map component and services, and your confirmation of the **constraints** our side has to rely on. Deployment, sizing, licensing and support of the map service are between HudHud and the Main Contractor; they are not in scope here.

The boundary we are working to is below. Please correct anything that does not match your understanding, since everything that follows rests on it.

- **HudHud provides** the map application, base-map data, map tiles and an embeddable map component, deployed on premises inside the MOI network.
- **Nexora-BI provides** the spatial **aggregates** only: grid-based counts, point-in-polygon assignment and hotspot intensity, computed in our data warehouse. We do not build a base map, a tile service or a map application.
- Our dashboards **embed** your component and pass those aggregates to it for rendering.

## Part A — Documents requested

1. **Component integration guide** — distribution form (JavaScript bundle, CSS, web component or iframe), exact version identifier, initialisation and configuration options, and the browser support matrix.
2. **Data layer input specification** — the accepted formats for a layer we supply (GeoJSON, vector tiles, WMS/WFS or a proprietary API), the direction of the interface (we push, or your component pulls from an endpoint we expose), and the schema for either case.
3. **Event and control API reference** — the events raised on user interaction, with their payloads, and the methods by which selection, viewport and layer visibility can be driven programmatically from the hosting page.
4. **Styling API** — how symbology for our own layers is set: colour ramp, opacity and class breaks.
5. **Coordinate reference statement** — the geodetic datum and EPSG code used by your base map and by the component's input and output, and which other CRS the component will accept.
6. **Administrative boundary and sector code list** — identifiers, hierarchy levels and the official source they follow, together with an export of the boundary polygons (GeoJSON or shapefile).
7. **Service interface specification** — endpoints our application tier will call, the authentication scheme, required ports and directions, and the health-check endpoint.
8. **Release notes** covering changes to items 2 through 5, for the versions proposed for this project.

## Part B — Constraints we need confirmed

These are the conditions our design depends on. Where one cannot be met, we need to know now rather than during integration — each has a consequence on our side that is cheap to handle today and expensive later.

9. **No external network access.** The component and everything it pulls in — styles, fonts, icons, tiles — must load from the internal HudHud service. Our virtual machines have no outbound internet access. Please confirm, or list any external hostname that is contacted.
10. **Version pinning.** We must be able to pin a component version and keep it working after the map service is updated. Please state the compatibility you can commit to for the APIs in Part A items 2–5.
11. **Declared, stable CRS.** The datum and EPSG code must be stated and must not change silently between versions.
12. **Right-to-left rendering.** The component must render correctly under an Arabic RTL layout — control placement, labels, legend, scale bar and tooltips. This is a functional requirement on our side, not a preference.
13. **Bilingual labelling.** Base-map features available in Arabic and English, with the display language selectable at run time.
14. **Historical boundaries.** Boundary changes must be versioned and published, so that a historical period can be reported using the boundaries in force at the time rather than today's.
15. **Unambiguous selection payload.** The event raised when a user selects a grid cell or feature must identify it unambiguously, so our dashboards can drill down to the underlying incident list.
16. **Detectable failure.** When the map service is unreachable, the component must raise a signal the hosting page can detect — not fail silently.
17. **Layer scale.** A layer of several thousand features per time window must be supported; please state the limits on feature count and request size, and the refresh cadence available without a page reload.
18. **Content Security Policy.** Please state the directives the component requires, so we can admit it without widening our policy further than necessary.
19. **Correlation identifier.** A request or correlation identifier carried through your service and recorded in its logs, so an incident can be traced across both systems.
20. **No undeclared telemetry.** Please confirm whether the component collects usage telemetry, and if so where it is sent.

WHY|Two of these carry more weight than the rest. On item 11: Saudi Arabia has several datums in active use, and if the CAD system and the map use different datums without transformation the positional error can reach tens of metres — enough to place an incident in the wrong sector while every component involved reports success. On item 16: our dashboards must degrade rather than fail, replacing the map panel with a table and a clear notice while the rest of the dashboard keeps working; without a detectable signal a map outage is shown to the user as a failure of the BI system.

## Part C — Points the documents may not answer

21. Whether your administrative and sector identifiers match those used by the CAD system, and if not, whether a mapping table exists.
22. If your CRS differs from the CAD system's, your recommended transformation path and its expected accuracy.
23. Anything you need from us that is not stated in the boundary above — formats, endpoints, identifiers or test data.

CLOSE|Documents may be attached against the Part A numbering; short written answers are sufficient for Parts B and C. Where a value is not yet fixed, please say so explicitly rather than omitting the item — an open point we know about is manageable, one discovered during integration is not.
