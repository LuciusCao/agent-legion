"""PostgreSQL CSV round-trip primitives for ``seed_from_prod``.

Protocol matrix with ``NULL '\\N'``: an unquoted ``\\N`` is SQL NULL, a quoted
``"\\N"`` is literal text, and an empty field is empty text. Python's csv reader
cannot retain the quoted/unquoted distinction, so parsing keeps it explicitly.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from typing import TextIO

CSVValue = str | None


def read_csv_rows(fh: TextIO) -> Iterator[list[CSVValue]]:
    row: list[CSVValue] = []
    field: list[str] = []
    quoted = False
    was_quoted = False
    pending = ""

    def finish_field() -> None:
        nonlocal field, was_quoted
        value = "".join(field)
        row.append(None if not was_quoted and value == "\\N" else value)
        field = []
        was_quoted = False

    while True:
        char = pending or fh.read(1)
        pending = ""
        if not char:
            if quoted:
                raise ValueError("unterminated quoted PostgreSQL CSV field")
            if field or row or was_quoted:
                finish_field()
                yield row
            return
        if quoted:
            if char == '"':
                following = fh.read(1)
                if following == '"':
                    field.append('"')
                else:
                    quoted = False
                    pending = following
            else:
                field.append(char)
            continue
        if char == ",":
            finish_field()
            continue
        if char in "\r\n":
            if char == "\r":
                following = fh.read(1)
                if following != "\n":
                    pending = following
            finish_field()
            yield row
            row = []
            continue
        if char == '"' and not field and not was_quoted:
            quoted = True
            was_quoted = True
        else:
            field.append(char)


class CSVDictReader:
    def __init__(self, fh: TextIO) -> None:
        self._rows = read_csv_rows(fh)
        header = next(self._rows, [])
        if any(column is None for column in header):
            raise ValueError("PostgreSQL CSV header contains NULL")
        self.fieldnames = [str(column) for column in header]

    def __iter__(self) -> Iterator[dict[str, CSVValue]]:
        for row in self._rows:
            if len(row) != len(self.fieldnames):
                raise ValueError("PostgreSQL CSV row has a different column count")
            yield dict(zip(self.fieldnames, row, strict=True))


def read_csv_dicts(fh: TextIO) -> Iterator[dict[str, CSVValue]]:
    return iter(CSVDictReader(fh))


def write_csv_row(fh: TextIO, values: Iterable[CSVValue]) -> None:
    writer = csv.writer(fh, lineterminator="")
    for index, value in enumerate(values):
        if index:
            fh.write(",")
        if value is None:
            fh.write("\\N")
        elif value == "\\N":
            fh.write('"\\N"')
        else:
            writer.writerow([value])
    fh.write("\n")
