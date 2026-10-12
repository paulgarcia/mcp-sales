"""Parameter types shared by the tools.

The descriptions end up in each tool's input schema, which is the only
documentation Claude reads before calling a tool. They say the format and
the default, because a model that has to guess a date format guesses wrong.
"""

from datetime import date
from typing import Annotated

from pydantic import Field

DateFrom = Annotated[
    date | None,
    Field(description="First local day of the range, YYYY-MM-DD. Default: 30 days before date_to."),
]
DateTo = Annotated[
    date | None,
    Field(description="Last local day of the range, inclusive, YYYY-MM-DD. Default: today."),
]
Brand = Annotated[
    str | None,
    Field(description="Only this brand. Case-insensitive, partial names match ('lumen')."),
]
IncludeInternal = Annotated[
    bool,
    Field(description="Include staff/QA test activity. Default false: tests are not business."),
]


Limit200 = Annotated[int, Field(ge=1, le=200, description="Rows to return. Max 200.")]
Limit500 = Annotated[int, Field(ge=1, le=500, description="Rows to return. Max 500.")]
