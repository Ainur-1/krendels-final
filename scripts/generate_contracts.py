"""Порождает модели, реестр, документацию и перечисления интерфейса из JSON Schema."""

from __future__ import annotations

from zero_defect.contracts.codegen import generate


def main() -> None:
    for path in generate():
        print(path)


if __name__ == "__main__":
    main()
