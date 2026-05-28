from __future__ import annotations

import unittest

from dxf_preview import build_dxf_render_geometry
from test_support import fixture_path


class DxfPreviewGeometryTests(unittest.TestCase):
    def test_build_dxf_render_geometry_covers_fixture_profile(self) -> None:
        geometry = build_dxf_render_geometry(fixture_path("profile_sample.dxf"))

        self.assertEqual(geometry.entity_count, 4)
        self.assertEqual(geometry.unsupported_count, 0)
        self.assertFalse(geometry.path.isEmpty())
        self.assertAlmostEqual(geometry.bounds.left(), 0.0)
        self.assertAlmostEqual(geometry.bounds.top(), -60.0)
        self.assertAlmostEqual(geometry.bounds.width(), 100.0)
        self.assertAlmostEqual(geometry.bounds.height(), 60.0)


if __name__ == "__main__":
    unittest.main()
