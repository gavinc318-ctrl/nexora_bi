# Information Request — GIS Interface (HudHud)

**Project:** OSS911 Makkah 911 Operations Centre — Business Intelligence and Data Warehouse subsystem
**From:** Nexora-BI
**Date:** 2026-10-07
**Requested reply by:** _[date]_

## 1. Context and division of responsibility

So that the questions below can be answered concisely, here is our understanding of the boundary.
Please correct anything that does not match yours.

- **HudHud provides** the map application, base map data, map tiles, and an embeddable map
  component, deployed **on premises** inside the MOI network.
- **Nexora-BI provides** the spatial *aggregates* only: grid-based counts, point-in-polygon
  assignment, and hotspot intensity, computed in our data warehouse. We do **not** build a base
  map, a tile service, or a map application.
- Our dashboards **embed** your component; the aggregates are passed to it for rendering.
- All our virtual machines have **no outbound internet access**. Everything must load from
  inside the network.

## 2. Deployment and versioning

1. Deployment form of the on-premises map service (container images, packages, or installer),
   and the supported host OS.
2. Version currently proposed, and the **release and support lifecycle** for the contract period
   (the contract runs 8 years).
3. How updates are delivered into an **air-gapped** network, and how their integrity is verified
   (checksums, signatures, and the signing key).
4. Resource requirements of the map service (vCPU, memory, storage, and expected growth of the
   tile/base-map data).

## 3. Embeddable map component

5. Distribution form of the component (JavaScript bundle, CSS, web component, iframe) and its
   exact version identifier.
6. Confirmation that the component, its styles, fonts, icons, and tiles all load **from the
   internal HudHud service** — no CDN, no external font or telemetry endpoint. If any external
   call exists, please list the hostnames.
7. Whether the component version can be **pinned** and served unchanged after a map-service update.
8. Browser support matrix, and whether the component works inside our portal's Content Security
   Policy (please state the directives it requires).
9. **Right-to-left (RTL) behaviour**: how the component renders under an Arabic RTL layout —
   control placement, labels, legends, scale bar, and tooltips. This is a functional requirement
   for us, not a preference.
10. Arabic and English labelling of base-map features, and how the display language is selected.

## 4. Coordinate reference system

11. The **geodetic datum and EPSG code** used by your base map and by your component's input
    and output (for example WGS 84 / EPSG:4326, KSA-GRF17, or Ain el Abd 1970).
12. Whether your component accepts data in a CRS other than its native one, and if so which.
13. If your CRS differs from the CAD system's, your recommended transformation path and its
    expected accuracy.

> Why this matters: Saudi Arabia has several datums in active use. If the CAD system and the map
> use different datums without transformation, the positional error can reach tens of metres —
> enough to place an incident in the wrong sector while every component reports success. We have
> parameterised the CRS on our side (source, storage, analysis) precisely so this can be set
> rather than assumed, but we need your value to set it.

## 5. Administrative boundaries and sector coding

14. The **coding scheme** for administrative divisions and police sectors used in your data
    (identifiers, hierarchy levels, and official source).
15. Whether these identifiers match the ones used by the CAD system. If not, is a mapping table
    available?
16. How often boundaries change, and how a boundary change is **versioned and published** —
    we must be able to report historical periods using the boundaries in force at the time,
    not today's.
17. Whether boundary polygons can be exported to us (GeoJSON or shapefile) for grid aggregation
    and reconciliation on our side.

## 6. Interface for our aggregate data

18. The **accepted input formats** for data layers we supply: GeoJSON, vector tiles, WMS/WFS,
    or a proprietary API. Please state the preferred one.
19. The interface form: do we **push** data to your service, or does your component **pull**
    from an endpoint we expose? Please provide the API specification for whichever applies.
20. Payload limits: maximum feature count, maximum request size, and recommended practice for
    a dense grid layer (our hotspot grid may contain several thousand cells per time window).
21. Supported refresh cadence, and whether a layer can be updated without reloading the page.
22. Styling control: can we control symbology (colour ramp, opacity, class breaks) for our
    layers, and through which API?

## 7. Interaction and drill-down

23. The **event API** of the component — specifically, the event raised when a user clicks a
    grid cell or a feature, and the payload it carries. Our dashboards must drill down from a
    map selection to the underlying incident list.
24. Whether selection state can be driven **programmatically** from our dashboard (for example,
    to highlight a sector chosen in a filter elsewhere on the page).

## 8. Availability and degradation

25. A **health-check endpoint** for the map service that we can poll.
26. Expected availability of the on-premises map service, and the planned maintenance windows.
27. Behaviour of the component when the map service is unreachable: does it fail silently,
    raise an event, or throw? We need a signal we can detect.

> Why this matters: our dashboards must **degrade, not fail**. When the map is unavailable, the
> panel is replaced by a table and a clear notice while the rest of the dashboard keeps working.
> A map failure must not be presented to the user as a failure of the BI system.

## 9. Security and network

28. Authentication between our portal/service layer and your map service (token, mTLS, or
    network-level trust only), and whether it integrates with the MOI identity provider.
29. Network ports and directions required between our application tier and your service.
30. Confirmation of how your service obtains any real-time information it needs from outside
    the MOI network, and through which zone — our understanding is that this path is yours and
    is operated independently of our subsystem.
31. Whether the component collects any usage telemetry, and if so where it is sent.

## 10. Capacity and performance

32. Expected response time for rendering a layer of the size described in item 20.
33. Concurrent-user capacity of the on-premises service at the proposed sizing.

## 11. Operations and support

34. Log locations and formats, for incident investigation across the two systems.
35. Support model and escalation path during the contract period.
36. Documentation set available to us: API reference, deployment guide, and release notes.

## 12. Commercial boundary

37. Confirmation of what is included in your scope: base-map data and its updates, tile
    service, map application, embeddable component, and on-premises deployment and maintenance.
38. Anything you expect **from us** that is not listed in section 1.

---

**Reply format.** Numbered answers against the items above are sufficient; documents may be
attached for items 19, 22, 23 and 36. Where a value is not yet fixed, please say so explicitly
rather than omitting the item — an open point we know about is manageable, one we discover
during integration is not.

**Internal reference:** this request serves CN-29, CN-32, DD-32 and DD-33; the answers to
items 11–13 fix `meta.srid_config`, and items 14–17 determine whether `dim_sector` can be
reconciled against the CAD sector assignment.
