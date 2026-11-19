from typing import Annotated

from pydantic import StringConstraints

Direction = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$"
)]
