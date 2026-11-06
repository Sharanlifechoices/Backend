"""Catalog item types, in a dependency-free module so core/ and modules/ can both import it."""

# Items the buyer downloads after paying (a PDF for a book, a ZIP of three books for a course).
# Everything that grants, lists, emails or protects a download must use this one tuple.
DELIVERABLE_TYPES = ("book", "course")


def is_deliverable(item_type: str) -> bool:
    return item_type in DELIVERABLE_TYPES
