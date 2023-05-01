import requests
import zipfile
import io
import argparse
import pandas


# usage: quellensteuer.py [-d OUTPUT_DIR]
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d", "--output_dir", required=True, type=str, help="Output directory."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Source: https://www.estv.admin.ch/estv/de/home/direkte-bundessteuer/dbst-quellensteuer/qst-tarife-kantone.html
    # Format: https://www.estv.admin.ch/dam/estv/de/dokumente/qst/schweiz/tar{YEAR}txt.zip.download.zip/tar{YEAR}txt.zip
    for year in ["2021", "2022", "2023"]:
        url = f"https://www.estv.admin.ch/dam/estv/de/dokumente/qst/schweiz/tar{year}txt.zip.download.zip/tar{year}txt.zip"
        zip_name = url.rsplit("/", 1)[-1]
        print(f"Downloading {zip_name}.")
        r = requests.get(url)
        print(f"Extracting {zip_name}.")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        z.extractall(args.output_dir)


if __name__ == "__main__":
    main()
