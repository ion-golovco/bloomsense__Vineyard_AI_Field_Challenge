import type { Feature, FeatureCollection, Geometry } from 'geojson';
export type MapProperties = {
  label: string;
  source?: string;
  vineyard_id?: string;
  row_id?: string;
  target_id?: string;
  reason?: string;
  area_m2?: number;
  length_m?: number;
  index?: string;
  dates?: string[];
  scope?: string;
  min_confidence?: number | null;
  confidence?: number;
  parcel_id?: string;
  field_share?: number;
  id?: string;
  gap_m?: number;
  // routes: per-target status, and the outside allowance (1.2% of the length) and what the route uses of it
  target_status?: TargetStatus[];
  outside_budget_m?: number;
  robust_outside_m?: number;
  targets?: number;  // routes: the visit points they were planned over, and how many they pass within 2 m
  visited?: number;
};
// visited within 2 m, over_budget (needs_outside_m: outside metres reaching it would add), unreachable or missed
export type TargetStatus = { id: string; status: string; distance_m: number; needs_outside_m: number | ''; reason: string };
export type MapFeature = Feature<Geometry, MapProperties>;
export type SatelliteIndex = 'ndvi' | 'ndmi';
export type SatelliteLegend = { min: number; max: number; pixel_m: number; colors: string[] };
// `date` is an ISO date or 'median' (per-pixel median of `dates`); `url` is a PNG data URL on `bounds`
export type SatelliteImage = { index: SatelliteIndex; date: string; site_median: number; url: string };
export type Satellite = {
  credit: string; flight: string; dates: string[]; bounds: [[number, number], [number, number]];
  legend: Record<SatelliteIndex, SatelliteLegend>; images: SatelliteImage[];
};
export type CadastreInfo = { credit: string; snapshot: string; count: number; in_fields: number };
// measurements.csv numbers in EPSG:32635 metres; an `estimate` field is measured from model predictions (not yet annotated)
export type MeasuredTotals = {
  row_count: number; row_length_m: number; canopy_area_m2: number; canopy_area_ha: number; interrow_area_m2: number; interrow_area_ha: number;
};
export type MeasuredBlock = MeasuredTotals & { vineyard_id: string; estimate: boolean };
export type MeasuredRow = { vineyard_id: string; row_id: string; length_m: number; row_structure: string };
export type Measurements = {
  estimate_blocks: number; total: MeasuredTotals & { block_count: number }; blocks: MeasuredBlock[]; rows: MeasuredRow[];
};
export type Scene = {
  crs: string; features: FeatureCollection<Geometry, MapProperties>; metrics?: { route_length_m?: number };
  sentinel?: Satellite | null; cadastre?: CadastreInfo | null; measurements?: Measurements;
};
export type AppView = 'per-field' | 'all-fields' | 'measurements' | 'analysis' | 'yield';
export type RouteEndpoint = { easting: number; northing: number; lon: number; lat: number; snapped_m: number };
// POST /api/route: the line is lon/lat for display, every metre is measured in EPSG:32635
export type PlannedRoute = {
  route: MapFeature; length_m: number; targets: number; visited: number; unreachable: number; over_budget: number;
  outside_share: number; hops: number; hop_m: number; legal: boolean; closed: boolean; open: boolean;
  start: RouteEndpoint; end: RouteEndpoint; compute_s: number;
  target_status: TargetStatus[];
};
