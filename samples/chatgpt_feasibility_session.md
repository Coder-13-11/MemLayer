---
project: Environmental AI Research
title: ChatGPT feasibility session — coastal wetland loss mapping (SYNTHETIC)
origin_model: chatgpt
conversation_id: synthetic-chatgpt-2026-10-28
source_kind: conversation
---
<!-- Synthetic test data written for the memlayer prototype. Not a real conversation. -->

## Objective
Map year-over-year coastal wetland loss along the Louisiana coast from 2017 to 2025 using free satellite imagery and a segmentation model that runs on a single consumer GPU.

## Findings
- [F1] Sentinel-2 provides free multispectral imagery at 10 m resolution
  The visible and near-infrared bands of Sentinel-2 are 10 m; red-edge and SWIR bands are 20 m.
  Level-2A surface-reflectance products are free to download.
  importance: high
  status: evidence_supported
  subject: sentinel2.best_resolution_m
  value: 10
  evidence: https://sentiwiki.copernicus.eu/web/s2-mission | Copernicus SentiWiki, Sentinel-2 mission overview

- [F2] Sentinel-2 revisits the same location every 5 days
  With both satellites (2A and 2B) in orbit, the combined revisit time at the equator is 5 days.
  importance: normal
  status: evidence_supported
  subject: sentinel2.revisit_days
  value: 5
  evidence: https://sentiwiki.copernicus.eu/web/s2-mission | Copernicus SentiWiki, revisit and coverage

- [F3] Labeled wetland polygons are available from the National Wetlands Inventory
  NWI publishes wetland boundary polygons for the US that can be rasterized into training masks.
  importance: high
  status: evidence_supported
  evidence: https://www.fws.gov/program/national-wetlands-inventory | US FWS National Wetlands Inventory

- [F4] A U-Net segmentation model is feasible on a single 12 GB consumer GPU
  256x256 tiles with 4-6 input bands and batch size 16 should fit in 12 GB of VRAM.
  importance: high

- [F5] Summer cloud cover removes a large share of usable scenes
  Roughly 40% of June-September scenes over the study area are expected to be unusable because of clouds.
  importance: normal

## Decisions
- [D1] Use Sentinel-2 Level-2A at 10 m as the primary input
  depends_on: F1, F2

- [D2] Train on NWI polygons as ground truth for 2019
  importance: high
  depends_on: A1

- [D3] Build one annual median composite per year to work around clouds
  depends_on: F5

## Assumptions
- [A1] NWI polygons are current enough to serve as 2019 ground truth
  The NWI update dates vary by area; this has not been checked for the study region.
  importance: high

- [A2] Wetland spectral signatures are stable across seasons
  importance: normal

## Open Questions
- [Q1] What accuracy do published studies report for wetland segmentation from Sentinel-2?
  importance: normal

- [Q2] How should tidal stage at acquisition time be handled?
  importance: high
