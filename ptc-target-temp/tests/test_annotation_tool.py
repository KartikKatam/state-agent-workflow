"""Tests for corner annotation tool.

Covers:
- JSON serialization/deserialization
- Corner data structure validation
- Split generation (deterministic, 80/20 ratio, stratification)
- Resume logic (skips already-annotated images)
- CLI argument parsing
- Coordinate conversion (screen → original pixel space)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.corner_annotator.annotate import (
    AnnotationState,
    build_parser,
    find_resume_index,
    generate_split,
    list_images,
    load_annotations,
    main,
    save_annotations,
)

# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def tmp_crops(tmp_path: Path) -> Path:
    """Create a temporary crops directory with dummy images."""
    crops = tmp_path / "crops"
    crops.mkdir()
    # Create small dummy PNG-like files (just need to exist for listing)
    for i in range(1, 6):
        (crops / f"crop_{i:05d}.png").write_bytes(b"fake")
    return crops


@pytest.fixture
def sample_annotations() -> dict:
    return {
        "crop_00001.png": {"corners": [[10.5, 20.3], [90.1, 20.7], [90.5, 60.2], [10.2, 60.8]]},
        "crop_00002.png": {"corners": [[5.0, 10.0], [85.0, 10.0], [85.0, 50.0], [5.0, 50.0]]},
    }


@pytest.fixture
def annotations_file(tmp_path: Path, sample_annotations: dict) -> Path:
    """Write sample annotations to a temp file."""
    p = tmp_path / "annotations.json"
    with open(p, "w") as f:
        json.dump(sample_annotations, f)
    return p


# ─── JSON Serialization/Deserialization ─────────────────────────────


class TestJsonIO:
    def test_save_and_load_roundtrip(self, tmp_path: Path, sample_annotations: dict) -> None:
        path = tmp_path / "ann.json"
        save_annotations(sample_annotations, path)
        loaded = load_annotations(path)
        assert loaded == sample_annotations

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"
        result = load_annotations(path)
        assert result == {}

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "dir" / "ann.json"
        save_annotations({"a": {"corners": [[1, 2]]}}, path)
        assert path.exists()
        loaded = load_annotations(path)
        assert "a" in loaded

    def test_floating_point_precision(self, tmp_path: Path) -> None:
        """Verify sub-pixel coordinates survive serialization."""
        annotations = {
            "img.png": {
                "corners": [[10.1234, 20.5678], [30.9012, 40.3456], [50.0, 60.0], [70.0, 80.0]]
            }
        }
        path = tmp_path / "ann.json"
        save_annotations(annotations, path)
        loaded = load_annotations(path)
        for orig, loaded_c in zip(
            annotations["img.png"]["corners"], loaded["img.png"]["corners"], strict=True
        ):
            assert orig[0] == pytest.approx(loaded_c[0])
            assert orig[1] == pytest.approx(loaded_c[1])

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "ann.json"
        save_annotations({"a": {"corners": []}}, path)
        save_annotations({"b": {"corners": []}}, path)
        loaded = load_annotations(path)
        assert "a" not in loaded
        assert "b" in loaded


# ─── Corner Data Structure ──────────────────────────────────────────


class TestCornerDataStructure:
    def test_corners_are_list_of_pairs(self, sample_annotations: dict) -> None:
        for data in sample_annotations.values():
            corners = data["corners"]
            assert isinstance(corners, list)
            assert len(corners) == 4
            for point in corners:
                assert isinstance(point, list)
                assert len(point) == 2

    def test_corners_are_numeric(self, sample_annotations: dict) -> None:
        for data in sample_annotations.values():
            for point in data["corners"]:
                for coord in point:
                    assert isinstance(coord, (int, float))

    def test_skipped_image_structure(self) -> None:
        skipped = {"corners": None, "skipped": True}
        assert skipped["corners"] is None
        assert skipped["skipped"] is True


# ─── Image Listing ──────────────────────────────────────────────────


class TestListImages:
    def test_lists_png_files_sorted(self, tmp_crops: Path) -> None:
        images = list_images(tmp_crops)
        assert len(images) == 5
        assert images == sorted(images)
        assert all(name.endswith(".png") for name in images)

    def test_ignores_non_image_files(self, tmp_crops: Path) -> None:
        (tmp_crops / "readme.txt").write_text("not an image")
        (tmp_crops / "data.json").write_text("{}")
        images = list_images(tmp_crops)
        assert len(images) == 5  # Still only the PNGs

    def test_includes_jpg(self, tmp_crops: Path) -> None:
        (tmp_crops / "extra.jpg").write_bytes(b"fake")
        images = list_images(tmp_crops)
        assert "extra.jpg" in images

    def test_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert list_images(empty) == []


# ─── Resume Logic ───────────────────────────────────────────────────


class TestResumeLogic:
    def test_resumes_from_first_unannotated(self) -> None:
        images = ["a.png", "b.png", "c.png", "d.png"]
        annotations = {
            "a.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            "b.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        assert find_resume_index(images, annotations) == 2

    def test_resumes_at_start_when_nothing_annotated(self) -> None:
        images = ["a.png", "b.png"]
        assert find_resume_index(images, {}) == 0

    def test_returns_len_when_all_annotated(self) -> None:
        images = ["a.png", "b.png"]
        annotations = {
            "a.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            "b.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        assert find_resume_index(images, annotations) == 2

    def test_handles_skipped_images(self) -> None:
        """Skipped images should count as annotated for resume purposes."""
        images = ["a.png", "b.png", "c.png"]
        annotations = {
            "a.png": {"corners": None, "skipped": True},
        }
        # b.png is first unannotated
        assert find_resume_index(images, annotations) == 1

    def test_gap_in_annotations(self) -> None:
        """If image 2 is annotated but image 1 is not, resume at image 1."""
        images = ["a.png", "b.png", "c.png"]
        annotations = {
            "b.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        assert find_resume_index(images, annotations) == 0


# ─── Annotation State ──────────────────────────────────────────────


class TestAnnotationState:
    def test_add_corner(self) -> None:
        state = AnnotationState(zoom=3)
        assert state.add_corner(30, 30) is True
        assert len(state.corners) == 1

    def test_max_four_corners(self) -> None:
        state = AnnotationState(zoom=3)
        for i in range(4):
            assert state.add_corner(i * 10, i * 10) is True
        assert state.add_corner(100, 100) is False
        assert len(state.corners) == 4

    def test_complete_property(self) -> None:
        state = AnnotationState(zoom=3)
        assert not state.complete
        for i in range(4):
            state.add_corner(i * 10, i * 10)
        assert state.complete

    def test_undo(self) -> None:
        state = AnnotationState(zoom=3)
        state.add_corner(10, 10)
        state.add_corner(20, 20)
        assert state.undo() is True
        assert len(state.corners) == 1

    def test_undo_empty(self) -> None:
        state = AnnotationState(zoom=3)
        assert state.undo() is False

    def test_reset(self) -> None:
        state = AnnotationState(zoom=3)
        state.add_corner(10, 10)
        state.add_corner(20, 20)
        state.reset()
        assert len(state.corners) == 0

    def test_coordinate_conversion(self) -> None:
        """Screen coords should map back to original pixel space correctly.

        With zoom=3, clicking at screen (4.5, 4.5) — the center of the
        pixel at original (1,1) — should give original coords (1.0, 1.0).
        Screen coordinate mapping: orig = screen/zoom - 0.5
        """
        state = AnnotationState(zoom=3)
        # Click at screen position (4.5 rounds to 4 or 5 in int coords)
        # center of original pixel (1,1) is at screen (1*3 + 1.5) = (4.5, 4.5)
        # But we get int screen coords, so (4, 4) or (5, 5)
        # screen=4: orig = 4/3 - 0.5 ≈ 0.8333
        # screen=5: orig = 5/3 - 0.5 ≈ 1.1667
        state.add_corner(5, 5)
        x, y = state.corners[0]
        # Should be close to original pixel (1, 1)
        assert 0.5 < x < 2.0
        assert 0.5 < y < 2.0

    def test_sub_pixel_precision(self) -> None:
        """Coordinates should be floating-point, not integers."""
        state = AnnotationState(zoom=3)
        state.add_corner(7, 7)
        x, y = state.corners[0]
        # 7/3 - 0.5 = 1.8333... → rounded to 4 decimal places
        assert isinstance(x, float)
        assert isinstance(y, float)


# ─── Split Generation ──────────────────────────────────────────────


class TestSplitGeneration:
    def test_deterministic_split(self, annotations_file: Path, tmp_path: Path) -> None:
        """Same seed produces identical splits."""
        split1 = generate_split(annotations_file, tmp_path / "s1.json", seed=42)
        split2 = generate_split(annotations_file, tmp_path / "s2.json", seed=42)
        assert split1 == split2

    def test_different_seed_may_differ(self, tmp_path: Path) -> None:
        """Different seeds can produce different splits (with enough data)."""
        # Need enough images to make different shuffles likely
        annotations = {
            f"img_{i:03d}.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]} for i in range(50)
        }
        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        split1 = generate_split(ann_path, tmp_path / "s1.json", seed=42)
        split2 = generate_split(ann_path, tmp_path / "s2.json", seed=99)
        # With 50 images, extremely unlikely to get the same shuffle
        assert split1 != split2

    def test_80_20_ratio(self, tmp_path: Path) -> None:
        """Train/val split should be approximately 80/20."""
        annotations = {
            f"img_{i:03d}.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]} for i in range(100)
        }
        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        split = generate_split(ann_path, tmp_path / "split.json", seed=42)
        assert len(split["train"]) == 80
        assert len(split["val"]) == 20

    def test_no_overlap_between_sets(self, tmp_path: Path) -> None:
        annotations = {
            f"img_{i:03d}.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]} for i in range(20)
        }
        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        split = generate_split(ann_path, tmp_path / "split.json", seed=42)
        train_set = set(split["train"])
        val_set = set(split["val"])
        assert train_set & val_set == set()
        assert train_set | val_set == set(annotations.keys())

    def test_skipped_images_excluded(self, tmp_path: Path) -> None:
        annotations = {
            "a.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            "b.png": {"corners": None, "skipped": True},
            "c.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        split = generate_split(ann_path, tmp_path / "split.json", seed=42)
        all_images = split["train"] + split["val"]
        assert "b.png" not in all_images
        assert len(all_images) == 2

    def test_stratified_by_night(self, tmp_path: Path) -> None:
        """When night metadata is present, split is stratified."""
        annotations = {}
        # 30 night images
        for i in range(30):
            annotations[f"night_{i:03d}.png"] = {
                "corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "night": True,
            }
        # 70 day images
        for i in range(70):
            annotations[f"day_{i:03d}.png"] = {
                "corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
            }

        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        split = generate_split(ann_path, tmp_path / "split.json", seed=42)

        # Check that night images appear in both train and val
        train_night = [n for n in split["train"] if n.startswith("night_")]
        val_night = [n for n in split["val"] if n.startswith("night_")]
        assert len(train_night) == 24  # 80% of 30
        assert len(val_night) == 6  # 20% of 30

        train_day = [n for n in split["train"] if n.startswith("day_")]
        val_day = [n for n in split["val"] if n.startswith("day_")]
        assert len(train_day) == 56  # 80% of 70
        assert len(val_day) == 14  # 20% of 70

    def test_split_file_written(self, annotations_file: Path, tmp_path: Path) -> None:
        split_path = tmp_path / "split.json"
        generate_split(annotations_file, split_path, seed=42)
        assert split_path.exists()
        with open(split_path) as f:
            data = json.load(f)
        assert "train" in data
        assert "val" in data

    def test_split_lists_are_sorted(self, tmp_path: Path) -> None:
        annotations = {
            f"img_{i:03d}.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]} for i in range(20)
        }
        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        split = generate_split(ann_path, tmp_path / "split.json", seed=42)
        assert split["train"] == sorted(split["train"])
        assert split["val"] == sorted(split["val"])

    def test_metadata_nested_night_flag(self, tmp_path: Path) -> None:
        """Night flag in metadata dict should also be recognized."""
        annotations = {
            "a.png": {
                "corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "metadata": {"night": True},
            },
            "b.png": {"corners": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        ann_path = tmp_path / "ann.json"
        with open(ann_path, "w") as f:
            json.dump(annotations, f)

        # Should not crash — just verifying stratification code handles nested metadata
        split = generate_split(ann_path, tmp_path / "split.json", seed=42)
        assert len(split["train"]) + len(split["val"]) == 2


# ─── CLI Parsing ────────────────────────────────────────────────────


class TestCLIParsing:
    def test_annotate_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["annotate", "/some/crops", "/out/ann.json"])
        assert args.command == "annotate"
        assert args.crops_dir == Path("/some/crops")
        assert args.output == Path("/out/ann.json")

    def test_annotate_zoom_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["annotate", "crops", "ann.json"])
        assert args.zoom == 3

    def test_annotate_zoom_custom(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["annotate", "crops", "ann.json", "--zoom", "2"])
        assert args.zoom == 2

    def test_split_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["split", "ann.json", "split.json"])
        assert args.command == "split"
        assert args.annotations == Path("ann.json")
        assert args.output == Path("split.json")

    def test_split_ratio_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["split", "ann.json", "split.json"])
        assert args.ratio == 0.8

    def test_split_ratio_custom(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["split", "ann.json", "split.json", "--ratio", "0.9"])
        assert args.ratio == 0.9

    def test_split_seed_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["split", "ann.json", "split.json"])
        assert args.seed == 42

    def test_no_command_returns_zero(self) -> None:
        """Running with no command should print help and return 0."""
        assert main([]) == 0

    def test_split_missing_file_returns_error(self, tmp_path: Path) -> None:
        result = main(["split", str(tmp_path / "missing.json"), str(tmp_path / "split.json")])
        assert result == 1

    def test_annotate_bad_dir_returns_error(self, tmp_path: Path) -> None:
        result = main(["annotate", str(tmp_path / "nonexistent"), str(tmp_path / "ann.json")])
        assert result == 1

    def test_split_end_to_end(self, annotations_file: Path, tmp_path: Path) -> None:
        """Full CLI split workflow."""
        split_path = tmp_path / "split.json"
        result = main(["split", str(annotations_file), str(split_path)])
        assert result == 0
        assert split_path.exists()
        with open(split_path) as f:
            data = json.load(f)
        assert len(data["train"]) + len(data["val"]) == 2
