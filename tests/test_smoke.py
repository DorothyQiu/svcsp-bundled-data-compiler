import svcsp_compiler


def test_package_importable() -> None:
    assert svcsp_compiler.__name__ == "svcsp_compiler"
