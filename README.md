# omni-cite: Unlocking References with Zotero, OneDrive, and Notion

[Showcase](https://www.notion.so/f1ba985954544885af4c5fbf0c9de3d5)

Omni-cite provides several scripts designed to greatly enhance management of references with Zotero. These scripts enable a workflow to efficiently process entries in your Zotero library to: 

- store all PDFs in one central directory which can be in the cloud, e.g. OneDrive (similar to the ZotFile extension)
- automatically extract custom information, such as:
    - a list of keywords to generate a wordcloud from the text
    - a list of Github links mentioned by the PDF
    - (assuming the PDF is stored on OneDrive) generate link to the file to share it with others or read/annotate it across platforms such as Android/iOS.
- upload any meta data (or the features mentioned above) to view in a Notion database (similar to the Notero extension)

## Setup

### Dependencies

There are three main dependencies, so sign up/in for each of the services:

- [Zotero](https://www.zotero.org/) (required) - used to collect all the references in your library and extract metadata (free account is fine) - after signing up, install the desktop app
    - [Zotero Connector](https://www.zotero.org/download/connectors) (recommended) - browser extension to conveniently add references to your Zotero library
    - [Zotfile](http://zotfile.com/) (optional) - automatically moves attached PDFs to a specified folder in the cloud (an omni-cite script does the same, but this is a Zotero extension which will run automatically as soon as an item is added)
- [OneDrive](https://www.microsoft.com/en-ww/microsoft-365/onedrive/online-cloud-storage) (recommended) - used to store the PDFs of the references and other data such as word clouds in the cloud (current offer includes 5 GB available with a free account)
    - [Microsoft Graph API](https://docs.microsoft.com/en-us/graph/use-the-api) (required) - to access and generate share links for PDFs on OneDrive
- [Notion](https://www.notion.so/) (recommended) - used to upload meta-data into a Notion database for cross platform viewing and browsing through your library, including advanced filtering and sorting options (free account available)

Furthermore, there are a few required python packages that can be installed through pip using `pip install -r requirements.txt`.

### APIs and Access

Omni-cite scripts use the APIs of the main dependencies above, which unfortunately requires a unique setup for each of the services. Although the scripts handle all the heavy lifting with the specific API requests, you need to provide a set of secrets and IDs for access.

First, create a copy of the file `secrets-template.yaml` in `config/` named `secrets.yaml`, and then as you collect the missing secrets, enter them into that file `config/secrets.yaml` replacing the corresponding comments. For any of the non-required services (e.g. OneDrive or Notion) that you do not want to use, delete the corresponding section in `config/secrets.yaml`.

- Zotero - to access your Zotero library through the API, you need a Zotero Library ID (mine is 6 digits, called `zotero-library` in the config), and an API key (an alphanumeric string, referred to as `zotero-api-key`). You can generate an API key [here](https://www.zotero.org/settings/keys/new), with additional information on authentication [here](https://www.zotero.org/support/dev/web_api/v3/basics). Be sure to give the key access to the library, notes, as well as write access. Then you should also see your userID (which omni-cite calls `zotero-library`), just above the “Create a new private key” button on the [Feeds/API](https://www.zotero.org/settings/keys) page.
- OneDrive - Microsoft has a dedicated service for the APIs of all their services including OneDrive called Microsoft Graph. Setting up the authentication can be relatively complicated (and honestly the setup instructions in their documentation is a little overwhelming). Fortunately, I found an excellent tutorial on YouTube [here](https://www.youtube.com/watch?v=1Jyd7SA-0kI) (note that omni-cite uses “Method 2” using a device flow for authentication) which you can follow to get all the necessary values. You need two values: an Application/Client ID (hexdecimal string, called `graph-app-id`) and a client secret (alphanumeric string, called `client-secret`). 
For a high-level summary: To get access you have to create an “app” and register it on [Azure](https://portal.azure.com/) (additional details [here](https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app#register-an-application)) where you should log in with the account with the OneDrive you want to use. As soon as you register you will see the client ID for `graph-app-id` under the display name labeled as “Application (client) ID”. Then you need to create a client secret in the “Certificates and secrets” tab (see [here](https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app#add-a-client-secret)) and then set that as `client-secret` in the config).
- Notion - now as excellent support for integrations to access and update Notion databases from Python. First, create your own Notion integration [here](https://www.notion.so/my-integrations) (and check “Read content”, “Update content”, and “Insert content” under “Content Capabilities”) and then enter the secret as `notion-secret`. Then create a new database (see showcase and the guide below for examples of the properties to automatically sync meta-data from Zotero). The hexadecimal database ID is the last component of the URL (ignoring all the parameters, i.e. after `?=`), and must be entered in the config under `notion-database-id`. Lastly, add the integration to the page by selecting “Share” and then invite the integration with “Can edit” access.

#### Security Considerations

As should be obvious, it’s important to keep all the values in `config/secrets.yaml` secret and not publish or upload them anywhere. For Zotero, the parameters mentioned above are all that is necessary to have full read and write access to your Zotero - so if you ever suspect that the secrets have been leaked, delete the key immediately [here](https://www.zotero.org/settings/keys). For OneDrive, the secrets alone are not sufficient for access since you need to generate a token using the device flow, and the generated token automatically expires after an hour. Note that to avoid having to regenerate the token before it expires, by default, it is stored as a local json file `onedrive-info.json`. Finally, for Notion, all pages where the integration has been invited, the secrets provide full read and write access, but the integration can be removed [here](https://www.notion.so/my-integrations). To view all the code associated with authentication see `src/auth.py` (Zotero and OneDrive) and `src/publish.py` (Notion).

## Recommended Usage

After setting everything up, for regular use, the suggested procedure is:

1. Add new items - The most convenient way to add new references to your library is using the [Zotero Connector](https://www.zotero.org/download/connectors) browser extension. When adding references from popular sources like arXiv, OpenReview, or IEEE, Zotero should automatically extract the metadata and even download the associated PDF file, which you can see using the Zotero desktop app. However, if you notice any item is missing a PDF, you can drag and drop the PDF file onto the item. If a PDF is available, it is strongly recommended to add it before moving on, and there should be at most one PDF file per item.
2. (optional) De-duplicate items - To make sure you don’t have any duplicate items in your library, the Zotero desktop app shows you a list of detected duplicate items. It’s recommended that you remove duplicates before proceeding.
3. Process new items - From this directory, run:
    
    ```python
    fig process update
    ```
    
    For each of the newly added items this script:
    
    - fixes missing URLs of books and movies
    - for academic papers
        - the matching Semantic Scholar entry is linked (if found)
        - the Google Scholar link is attached
    - if no PDF is attached, but a website snapshot is attached, then the snapshot is converted to a PDF
    - if exactly one PDF file is attached (regardless of whether it’s an imported or linked file)
        - the PDF is renamed based on the metadata (including author, year, and title)
        - the PDF is moved to the specified directory `zotero-cloud` (should be in OneDrive, defaults to `$HOME/OneDrive/Papers/zotero`)
        - all GitHub links in the PDF are extracted and added to a note called “Code Links”
        - a word cloud is generated from all the text in the PDF and saved in the directory specified with `wordcloud-root` (defaults to `$HOME/OneDrive/Papers/wordclouds`)
    
    Before moving on to the next step, wait some time for all the attachments to be uploaded to the cloud.
    
4. Create OneDrive share links - From this directory, run:
    
    ```bash
    fig sharing update
    ```
    
    For each new item with a PDF uploaded to OneDrive, this will set the URL of the Zotero entry to the OneDrive URL of the PDF file. Then, for each word cloud image saved on OneDrive, a download share link is created to enable embedding the word cloud in a webpage (used for Notion) and set as the word cloud’s URL in Zotero.
    
    Particularly if you’re adding many new items at once, the Graph API does have notable [request limits](https://docs.microsoft.com/en-us/graph/throttling). At least for me, after creating 15-30 download links for word clouds, the Graph API starts sending 429 errors. If/when that happens, you may have to wait a few hours and then retry. You can also optionally include the argument `--limit 10` in the command above to process the links in smaller batches.
    
5. Upload new Zotero entries to a Notion database - From this directory, run:
    
    ```bash
    fig publish update
    ```
    
    By default, this will only upload entries that are already have OneDrive links for the PDF and word cloud. However, if you are not using OneDrive, or don’t need those links to be included in Notion, then you can 
    

## Documentation

[coming soon]

## Bibtex

If you like this work and make use of it, please cite our work as follows:

```bash
@misc{leeb2022omnicite,
  title =        {Omni-cite},
  author =       {Leeb, Felix},
  howpublished = {\url{https://github.com/felixludos/omni-cite}},
  year =         {2022}
}
```