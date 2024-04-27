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


# Source: https://www.estv.admin.ch/estv/de/home/direkte-bundessteuer/dbst-quellensteuer/qst-tarife-kantone.html
def get_qlt_zip_url_pre_2024(year: int) -> str:
    return f"https://www.estv.admin.ch/dam/estv/de/dokumente/qst/schweiz/qst-ch-tar{year}-de.zip.download.zip/qst-ch-tar{year}-de.zip"

def get_qlt_zip_url_from_2024(year: int) -> str:
    return f"https://www.estv.admin.ch/dam/estv/de/dokumente/qst/schweiz/tar{year}.zip.download.zip/tar{year}.zip"

def main():
    args = parse_args()

    year_format_mapping = {
        2021: get_qlt_zip_url_pre_2024,
        2022: get_qlt_zip_url_pre_2024,
        2023: get_qlt_zip_url_pre_2024,
        2024: get_qlt_zip_url_from_2024
    }  

    for year, format_func in year_format_mapping.items():
        url = format_func(year) # get the download URL
        zip_name = url.rsplit("/", 1)[-1] # get the last part of the url

        print(f"Downloading {zip_name}.")
        r = requests.get(url)
        if r.status_code != 200:
            raise Exception(f"Failed to download {zip_name}. Error: {r.status_code}")
        
        print(f"Extracting {zip_name}.")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        # error if all files within the zip file are not also zip files
        if not all([f.filename.endswith(".zip") for f in z.filelist]):
            raise Exception(f"Unexpected contents of {zip_name}.")
        # iterate through file list of the parent zip file and extract the nested zips
        for f in z.filelist:
            nested_zip_file = z.open(f)
            nested_zip = zipfile.ZipFile(nested_zip_file)
            nested_zip.extractall(args.output_dir)
            nested_zip.close()
            nested_zip_file.close()

if __name__ == "__main__":
    main()
