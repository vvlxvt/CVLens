from pathlib import Path

import pdfplumber

FILES = [
    Path(r"C:\Users\vital\Downloads\CV Vitalii Volokhatiuk python developer\Vitaliy_Volokhatiuk_Python_Developer.pdf"),
    Path(r"C:\Users\vital\Downloads\CV Vitalii Volokhatiuk python developer\Vitaliy_Volokhatiuk_Python_Developer_no_parser_3.pdf"),
    Path(r"C:\Users\vital\Downloads\CV Vitalii Volokhatiuk python developer\Vitaliy_Volokhatiuk_Python_Developer_new_2.pdf"),
    Path(r"C:\Users\vital\Downloads\CV Vitalii Volokhatiuk python developer\Vitaliy_Volokhatiuk_Python_Developer_new_1.pdf"),
    Path(r"C:\Users\vital\Downloads\CV Vitalii Volokhatiuk python developer\Vitaliy_Volokhatiuk_Python_Developer_0.pdf"),
]


for path in FILES:
    print("\n" + "=" * 100)
    print(path.name)
    with pdfplumber.open(path) as pdf:
        print(f"PAGES: {len(pdf.pages)}")
        for number, page in enumerate(pdf.pages, 1):
            print(f"\n--- PAGE {number} ---")
            print(page.extract_text(x_tolerance=2, y_tolerance=3) or "[NO EXTRACTABLE TEXT]")
