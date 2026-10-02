from pathlib import Path

from colormatcher.services.naming import Naming, output_path, split_version


def test_split_version():
    assert split_version("img_v012") == ("img", 12)
    assert split_version("img") == ("img", 0)
    assert split_version("a_v1_b") == ("a_v1_b", 0)


def test_version_increments(tmp_path):
    src = Path("C:/x/фото.jpg")
    first = output_path(src, tmp_path, Naming.version)
    assert first.name == "фото_v001.jpg"
    first.touch()
    (tmp_path / "фото_v004.png").touch()
    assert output_path(src, tmp_path, Naming.version).name == "фото_v005.jpg"


def test_version_of_versioned_source(tmp_path):
    assert output_path(Path("p_v007.jpg"), tmp_path, Naming.version).name == "p_v008.jpg"


def test_overwrite_and_suffix(tmp_path):
    src = Path("C:/x/a.png")
    assert output_path(src, tmp_path, Naming.overwrite) == tmp_path / "a.png"
    assert output_path(src, tmp_path, Naming.suffix, suffix="_ref").name == "a_ref.png"
    assert output_path(src, None, Naming.overwrite) == src
