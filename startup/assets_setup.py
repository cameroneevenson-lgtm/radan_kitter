from __future__ import annotations

import assets


def configure_assets(*, w_release_root: str, eng_release_map: dict) -> None:
    assets.configure_release_mapping(
        w_release_root=w_release_root,
        eng_release_map=eng_release_map,
    )
    assets.load_asset_root_preferences()
