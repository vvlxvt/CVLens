from pathlib import Path

from extract.parser import clean_cv_text, extract_pdf_text

DATA_DIR = Path(__file__).resolve().parent
file_url = "Vitaliy_Volokhatiuk_Python_Developer.pdf"
# file_url = DATA_DIR / "CV (6).pdf"


if __name__ == "__main__":
    text = extract_pdf_text(file_url, data_dir=DATA_DIR)
    data = clean_cv_text(text)
    print(data)

