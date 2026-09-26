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
};
export type MapFeature = Feature<Geometry, MapProperties>;
export type Scene = { crs: string; features: FeatureCollection<Geometry, MapProperties>; metrics?: { route_length_m?: number } };
export type AppView = 'per-field' | 'all-fields' | 'analysis' | 'yield';
