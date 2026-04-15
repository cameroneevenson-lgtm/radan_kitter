from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

import assets
import radan_kitter


class MainResolverPolicyTests(unittest.TestCase):
    def test_build_packet_uses_fast_asset_resolution(self) -> None:
        stub = SimpleNamespace(tree=object(), parts=[], rpd_path="job.rpd")

        with mock.patch("radan_kitter.ui_actions.run_build_packet") as run_build_packet:
            radan_kitter.Main.build_packet_only(stub)

        self.assertIs(run_build_packet.call_args.kwargs["resolve_asset_fn"], assets.resolve_asset_fast)

    def test_rf_suggest_uses_fast_asset_resolution(self) -> None:
        stub = SimpleNamespace(tree=object(), model=object(), parts=[], rpd_path="job.rpd")

        with mock.patch("radan_kitter.ui_actions.run_rf_suggest") as run_rf_suggest:
            radan_kitter.Main.run_rf_suggestions(stub)

        self.assertIs(run_rf_suggest.call_args.kwargs["resolve_asset_fn"], assets.resolve_asset_fast)

    def test_ml_log_uses_fast_asset_resolution(self) -> None:
        stub = SimpleNamespace(
            tree=object(),
            parts=[],
            rpd_path="job.rpd",
            _refresh_ml_plot_pane=lambda: None,
        )

        with mock.patch("radan_kitter.ui_actions.run_ml_log") as run_ml_log:
            radan_kitter.Main.run_ml_log(stub)

        self.assertIs(run_ml_log.call_args.kwargs["resolve_asset_fn"], assets.resolve_asset_fast)


if __name__ == "__main__":
    unittest.main()
