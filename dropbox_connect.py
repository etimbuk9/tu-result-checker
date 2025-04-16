import dropbox
from dotenv import load_dotenv
import os
import pandas as pd
import urllib
import ssl

# Load environment variables from .env file
load_dotenv()

# Get the Dropbox access token from environment variables
DROPBOX_ACCESS_TOKEN = os.getenv('DROPBOX_KEY')
if not DROPBOX_ACCESS_TOKEN:
    raise ValueError("DROPBOX_KEY environment variable not set.")

# Initialize Dropbox client
dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)


def getFileInformation(filePath):
    try:
        shared_link_metadata = dbx.sharing_create_shared_link_with_settings(
            filePath)
    except:
        shared_link_metadata = dbx.sharing_create_shared_link(filePath)

    shared_link = shared_link_metadata.url

    return shared_link.replace('dl=0', 'dl=1')


def get_result_url(session, semester):
    folder = '/FinalResults'
    files = dbx.files_list_folder(folder).entries
    files = [file for file in files if isinstance(
        file, dropbox.files.FileMetadata) and file.name == f"final-result-{session}-{semester}.csv"]

    if files:

        file = files[0]

        file_url = getFileInformation(folder + '/' + file.name)
        return file_url

    return None


def get_student_result(url, student_no):
    context = ssl._create_unverified_context()

    # Open the URL manually and read into pandas
    with urllib.request.urlopen(url, context=context) as response:
        df = pd.read_csv(response)

    if df.empty:
        return None

    df = df[df['student_name'] == int(student_no)]

    if not df.empty:
        return df

    return None


def main():

    session = '2023/2024'
    session = str(session).replace('/', '-')

    semester = 'First Semester'
    student_no = 202100001
    url = get_result_url(session, semester)

    if url:
        result = get_student_result(url, student_no)
        if result is not None:
            return result
        else:
            return {"detail": "Student result not found."}


if __name__ == "__main__":
    main()
