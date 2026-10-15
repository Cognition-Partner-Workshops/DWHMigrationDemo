"""ETL output formats and static syntax validation."""

from migraai.etl.outputs.formats import FORMATS, TARGETS, OutputFormat, formats_for, get_format
from migraai.etl.outputs.sources import FORMAT_SOURCES
from migraai.etl.outputs.validate import validate_output

__all__ = [
    "OutputFormat",
    "FORMATS",
    "FORMAT_SOURCES",
    "TARGETS",
    "formats_for",
    "get_format",
    "validate_output",
]
