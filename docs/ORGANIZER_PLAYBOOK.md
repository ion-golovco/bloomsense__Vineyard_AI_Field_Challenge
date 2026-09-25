# Organizer playbook (as given to teams, 25 September 2026)

Pasted by the team from the organizers' step-by-step. Where it differs from `docs/SPEC.md`, this is newer.

**Onboarding**
1. Log in at marcaj.com/login with the credentials from the email. The team project already exists, and its labels are configured.
2. Read the annotation rules and the Marcaj quick-start in Assets. Download the 5 tile ZIPs (311 GeoTIFFs), plus the start and the passage/forbidden GeoJSONs.
3. Split roles, for example 1–2 on ML (canopies, rows, inter-rows, waste), 1 on GIS/route and 1 on the web interface. On Saturday everyone annotates.

**Friday night to Saturday midday: pre-annotations (ML)**
- Build a pipeline that turns tiles into vineyard polygons, row polylines, `interrow_area` polygons, waste boxes and attributes. `vineyard_id` and `row_id` must stay the same across tile edges.
- Write the output as CVAT for images 1.1: `annotations.xml` plus `images/` with the original tiles, names unchanged. The ZIP in `05_examples` is the template.
- Dry run first: upload the example ZIP to the project, check that it imports correctly, then delete it.
- ⚠️ Pre-annotations can be imported only once, before Publish. **Aim to import and publish by Saturday ~14:00**, which leaves a day for manual correction.

**In parallel from the start: route and web (don't wait for the model)**
- Route: build a graph over `interrow_area` plus passages, avoiding canopies (`vineyard`) and forbidden zones. It visits the inspection targets and waste and returns to the start (±5 m). All coordinates are in EPSG:32635. Develop it on the example annotations.
- Web: a map showing the route with its length, canopies and inter-rows, IDs, counts and row lengths.

**Saturday: annotate in Marcaj**
1. Upload all 5 parts, check there are 311 files, then Publish.
2. Split the jobs within the team, correct geometry and attributes, and Submit every job. Unsubmitted jobs score zero.
3. Give neighbouring tiles to the same person. After publishing, don't add or delete tiles and don't change the labels.

**Sunday morning: finish**
- Export the corrected annotations from Marcaj and recompute `route.geojson` and `measurements.csv` from them.
- README: install/run steps, pinned dependencies, weights link, processing time and hardware, any paid APIs.
- A 5-minute pitch with a live demo of the web interface.
- 15:00 Sunday: the repo and the Marcaj project are frozen.

**Where the points are:** canopies 25 · route 25 · rows + attributes 15 · measurements 10 · waste 10 · engineering 15.
