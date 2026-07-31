import sys

from .cli import categories, compare, scrape

match sys.argv[1:]:
    case ["scrape"]:
        scrape()
    case ["compare", qty, *words] if qty.isdigit() and words:
        compare(" ".join(words), int(qty))
    case ["compare", *words] if words:
        compare(" ".join(words))
    case ["categories", *words]:
        categories(" ".join(words))
    case _:
        print("usage: python -m price_comparator scrape\n"
              "       python -m price_comparator compare [qty] <query>\n"
              "       python -m price_comparator categories [keyword]")
