import zipfile

from tools.build_upgrade_deliverables import DOCX_ZIP_TIMESTAMP, save_reproducible_docx


class _VaryingZipDocument:
    def __init__(self):
        self.run = 0

    def save(self, path):
        self.run += 1
        timestamp = (2026, 8, 1, 12, 0, self.run * 2)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in reversed((
                ("[Content_Types].xml", b"<types />"),
                ("word/document.xml", b"<document>stable</document>"),
            )):
                info = zipfile.ZipInfo(name, timestamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)


def test_save_reproducible_docx_normalizes_zip_metadata_and_order(tmp_path):
    document = _VaryingZipDocument()
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"

    save_reproducible_docx(document, first)
    save_reproducible_docx(document, second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert {info.date_time for info in archive.infolist()} == {DOCX_ZIP_TIMESTAMP}
