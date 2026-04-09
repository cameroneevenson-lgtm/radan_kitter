# Graph Report - C:\Tools\radan_kitter  (2026-04-08)

## Corpus Check
- 47 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 543 nodes · 957 edges · 22 communities detected
- Extraction: 62% EXTRACTED · 38% INFERRED · 0% AMBIGUOUS · INFERRED: 364 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `Main` - 35 edges
2. `PartRow` - 24 edges
3. `PdfPreviewView` - 23 edges
4. `compute_dxf_features()` - 20 edges
5. `PartsModel` - 18 edges
6. `_normalize_path()` - 17 edges
7. `NumpadController` - 16 edges
8. `main()` - 11 edges
9. `run_scan_and_log()` - 10 edges
10. `PreviewCoordinator` - 10 edges

## Surprising Connections (you probably didn't know these)
- `Convert to black/white while preserving:     - red content gated by symbol/dimen` --uses--> `PartRow`  [INFERRED]
  C:\Tools\radan_kitter\pdf_packet.py → C:\Tools\radan_kitter\rpd_io.py
- `Convert a pixmap to plain grayscale (no threshold / no invert).` --uses--> `PartRow`  [INFERRED]
  C:\Tools\radan_kitter\pdf_packet.py → C:\Tools\radan_kitter\rpd_io.py
- `Create a concatenated packet and stamp QTY in bottom-left on each page.     Rend` --uses--> `PartRow`  [INFERRED]
  C:\Tools\radan_kitter\pdf_packet.py → C:\Tools\radan_kitter\rpd_io.py
- `Scan parts, append labels/features to dataset, and log run artifacts.      Retur` --uses--> `ScanLogger`  [INFERRED]
  C:\Tools\radan_kitter\ml_pipeline.py → C:\Tools\radan_kitter\ml_dataset_store.py
- `Recompute ML feature columns for every existing row in dataset_path.      Uses e` --uses--> `ScanLogger`  [INFERRED]
  C:\Tools\radan_kitter\ml_pipeline.py → C:\Tools\radan_kitter\ml_dataset_store.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (32): kit_file_path_for_part_sym(), kit_text_for_rpd(), Sort text the way Windows Explorer typically orders file names:     numeric runs, sanitize_kit_name(), windows_natural_sort_key(), apply_balance_and_update_kit_texts(), prepare_kits(), _bring_to_front() (+24 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (18): Exception, apply_packet_result(), _format_error(), _mark_apply_failure(), _apply_packet_result(), build_watermarked_packet(), _draw_dim_mask(), _draw_rounded_stroke_rect() (+10 more)

### Community 2 - "Community 2"
Cohesion: 0.1
Nodes (26): ScanLogger, _clamp01(), _compute_dxf_features(), _compute_ml_log_row(), _compute_pdf_features_vector(), compute_phase2_signals(), _compute_phase2_signals_detail(), ensure_dataset_exists() (+18 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (16): _apply_preferred_layers(), _CacheEntry, _norm_layer_name(), PdfPreviewView, Rendering-only PDF preview:     - caches rendered QImage/QPixmap per (pdf_path,, Set the PDF to display. If the path changes, render once and cache., Changes DPI and clears cached renders so next preview is regenerated., Limit total cached preview images to roughly cache_mb megabytes. (+8 more)

### Community 4 - "Community 4"
Cohesion: 0.09
Nodes (19): apply_packet_layer_policy(), collect_layer_zero_masks(), erase_layer_zero_overlays(), first_toggle_layer_aliases(), is_layer_zero_name(), is_packet_target_layer_name(), is_symbol_or_dimension_layer_name(), matches_zero_layer_alias() (+11 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (10): append_labeled_row(), ensure_dataset_exists(), load_dataset_df(), load_existing_part_names(), make_part_key(), normalize_identity_path(), part_keys_from_df(), Append/Upsert one labeled row to the dataset.      Global identity key: part_key (+2 more)

### Community 6 - "Community 6"
Cohesion: 0.1
Nodes (11): MlRecomputeWorker, MlRecomputeWorkerSignals, MlScanWorker, MlStats, MlWorkerSignals, Welford, PacketBuildWorker, PacketWorkerSignals (+3 more)

### Community 7 - "Community 7"
Cohesion: 0.12
Nodes (2): QMainWindow, Main

### Community 8 - "Community 8"
Cohesion: 0.12
Nodes (22): _acquire_single_instance_lock(), _clear_reload_handshake(), _diff_paths(), _is_ignored_dir(), _iter_watch_files(), main(), Return True if user wants to restart, False to exit launcher., Acquire a process-wide mutex so only one hot-reload launcher runs per app root. (+14 more)

### Community 9 - "Community 9"
Cohesion: 0.1
Nodes (7): QAbstractTableModel, QStyledItemDelegate, Clean, stable Main window:     - Open RPD -> table     - Crisp single-page PDF p, KitComboDelegate, PartsModel, PrioritySpinDelegate, PreviewCoordinator

### Community 10 - "Community 10"
Cohesion: 0.23
Nodes (24): _candidate_asset_dirs(), _candidate_filenames(), _candidate_search_roots(), _clear_search_cache(), configure_release_mapping(), _ensure_settings_dir(), _extract_fnum_tail(), get_asset_root_state() (+16 more)

### Community 11 - "Community 11"
Cohesion: 0.19
Nodes (23): _arc_points(), _arc_span_deg(), _bbox_aspect(), _bbox_from_points(), _circle_points(), compute_dxf_features(), _convex_hull(), _dedupe_consecutive_points() (+15 more)

### Community 12 - "Community 12"
Cohesion: 0.14
Nodes (13): atomic_write_bytes(), backup_file(), ensure_dir(), ensure_parent_dir(), now_stamp(), build_kit_sym_from_donor(), donor_extract_placeholder_paths(), _extract_slot_blocks() (+5 more)

### Community 13 - "Community 13"
Cohesion: 0.19
Nodes (11): QWidget, _boost_logo_tile(), build_main_layout(), _clamp8(), _make_letterbox_texture_pixmap(), _make_tiled_banner_pixmap(), _trim_transparent_padding(), _format_kit_label() (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.21
Nodes (15): QDialog, _AspectLockedDialog, _coerce_numeric(), create_polar_dialog(), _dataset_cache_key(), _draw_signal_grid(), _finite(), _interp_closed_signal() (+7 more)

### Community 15 - "Community 15"
Cohesion: 0.19
Nodes (8): force_w_candidates(), map_to_eng_release(), Map a symbol path to a release path using ENG_RELEASE_MAP when possible., Generate W:-first candidates for pdf/dxf resolution.     Logic:       - Derive p, Resolve an asset path (PDF/DXF) from a symbol path.     ext should be '.pdf' or, resolve_asset(), unique_norm_paths(), PacketPathsTests

### Community 16 - "Community 16"
Cohesion: 0.42
Nodes (1): NumpadController

### Community 17 - "Community 17"
Cohesion: 0.26
Nodes (9): _load_dataset_for_rf(), _model_key(), predict_with_rf(), Returns (model, encoder, feature_names, source)     source in {"memory", "disk",, Return a pruned feature matrix and selected feature names by removing:     - con, _safe_float(), _select_uncorrelated_features(), train_or_load_rf() (+1 more)

### Community 18 - "Community 18"
Cohesion: 0.18
Nodes (3): format_prompt_message(), remaining_seconds(), HotReloadServiceTests

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (7): collect_red_symbol_dimension_chars(), color_to_rgb(), highlight_red_target_layers(), highlight_red_text(), is_red_rgb(), is_red_text_color(), looks_like_dimension_text()

### Community 20 - "Community 20"
Cohesion: 0.52
Nodes (6): compute_pdf_features_vector(), _format_error(), _is_red(), _layer_matches(), _page_area(), _to_rgb255()

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **26 isolated node(s):** `Sort text the way Windows Explorer typically orders file names:     numeric runs`, `Return True if user wants to restart, False to exit launcher.`, `Acquire a process-wide mutex so only one hot-reload launcher runs per app root.`, `Append/Upsert one labeled row to the dataset.      Global identity key: part_key`, `Return normalized aliases for the first toggleable UI layer entry.     Exporter-` (+21 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 21`** (1 nodes): `move_console_to_screen2.ps1`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PartRow` connect `Community 6` to `Community 0`, `Community 1`, `Community 9`, `Community 7`?**
  _High betweenness centrality (0.143) - this node is a cross-community bridge._
- **Why does `Main` connect `Community 7` to `Community 0`, `Community 9`, `Community 16`, `Community 6`?**
  _High betweenness centrality (0.136) - this node is a cross-community bridge._
- **Why does `PreviewCoordinator` connect `Community 9` to `Community 0`, `Community 3`, `Community 6`, `Community 7`, `Community 13`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Main` (e.g. with `PartsModel` and `NumpadController`) actually correct?**
  _`Main` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `PartRow` (e.g. with `load_rpd()` and `Welford`) actually correct?**
  _`PartRow` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `compute_dxf_features()` (e.g. with `_format_error()` and `_iter_dxf_entities()`) actually correct?**
  _`compute_dxf_features()` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `PartsModel` (e.g. with `Main` and `Clean, stable Main window:     - Open RPD -> table     - Crisp single-page PDF p`) actually correct?**
  _`PartsModel` has 6 INFERRED edges - model-reasoned connections that need verification._