"""
EPUB Translator
Author: Quan Tran
Version: 1.0.3
"""

import argparse
import os
import re
import shutil
import sys
import zipfile
import logging
from pathlib import Path
from multiprocessing.dummy import Pool as ThreadPool

import requests
import tqdm
from bs4 import BeautifulSoup as bs
from bs4 import element
from google_trans_new import google_translator

# Setup logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

TOOL_VERSION = '1.0.3'
LINE_SIZE = 90
HEADERS = {
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
}


class TranslatorEngine:
    def __init__(self, dest_lang='vi', dict_path=None):
        self.dest_lang = dest_lang
        self.translation_dict = {}
        self.dict_format = '^[^:]+:[^:]+$'
        self.max_trans_chars = 4500
        self.translator = google_translator(timeout=5)
        self.file_path = ''
        self.file_name = ''
        self.file_extracted_path = ''
        self.html_list_path = []

        if dict_path:
            self.translation_dict_file_path = os.path.abspath(dict_path)
            if not self.load_translation_dict():
                sys.exit(1)

    def check_for_tool_updates(self):
        try:
            response = requests.get(
                'https://api.github.com/repos/quantrancse/epub-translator/releases/latest',
                headers=HEADERS, timeout=5).json()
            latest_release = response.get('tag_name', '')[1:]
            if TOOL_VERSION != latest_release:
                logging.warning(
                    f"Current version: {TOOL_VERSION}, Latest version: {latest_release}")
                logging.warning(
                    "Please upgrade from: https://github.com/quantrancse/epub-translator/releases")
        except Exception:
            logging.warning("Could not check for updates.")

    def load_translation_dict(self):
        if not os.path.isfile(self.translation_dict_file_path):
            logging.error("Invalid dictionary file path.")
            return False

        with open(self.translation_dict_file_path, encoding='utf-8') as f:
            for line in f:
                if re.match(self.dict_format, line):
                    key, value = line.strip().split(':', 1)
                    self.translation_dict[key] = value
                else:
                    logging.error(
                        f"Invalid format in dictionary: {line.strip()}")
                    return False
        logging.info("Translation dictionary loaded.")
        return True

    def get_epub_file_info(self, file_path):
        self.file_path = file_path
        self.file_name = Path(file_path).stem
        self.file_extracted_path = os.path.join(
            Path(file_path).parent, self.file_name + '_translated')

    def extract_epub(self):
        try:
            with zipfile.ZipFile(self.file_path, 'r') as zip_ref:
                logging.info("Extracting epub file...")
                zip_ref.extractall(self.file_extracted_path)
            return True
        except Exception as e:
            logging.error(f"Failed to extract epub: {e}")
            return False

    def get_epub_html_paths(self):
        self.html_list_path = [
            str(p.resolve())
            for ext in ['*.html', '*.htm', '*.xhtml']
            for p in Path(self.file_extracted_path).rglob(ext)
        ]

    def multithread_translate_html(self):
        pool = ThreadPool(8)
        try:
            list(tqdm.tqdm(pool.imap_unordered(self.translate_html, self.html_list_path), total=len(
                self.html_list_path), desc='Translating'))
        finally:
            pool.close()
            pool.join()

    def translate_html(self, html_file):
        try:
            with open(html_file, encoding='utf-8') as f:
                soup = bs(f, 'xml')

            text_nodes = [
                ele for ele in soup.descendants
                if isinstance(ele, element.NavigableString) and ele.strip() not in ['', 'html']
            ]

            original_texts = [str(ele) for ele in text_nodes]
            translated_texts = self.translate_tag(original_texts)

            for ele, translated in zip(text_nodes, translated_texts):
                ele.replace_with(element.NavigableString(
                    self.replace_translation_dict(translated)))

            with open(html_file, "w", encoding="utf-8") as w:
                w.write(str(soup))
        except Exception as e:
            logging.error(f"Error translating {html_file}: {e}")

    def replace_translation_dict(self, text):
        for key, val in self.translation_dict.items():
            text = text.replace(key, val)
        return text

    def translate_tag(self, text_list):
        chunks = self.chunk_text(text_list)
        translations = self.parallel_translate(chunks)
        return self.extract_chunks(translations)

    def translate_text(self, text):
        for attempt in range(3):
            try:
                return self.translator.translate(text, self.dest_lang)
            except Exception as e:
                logging.warning(
                    f"Translation failed (attempt {attempt + 1}): {e}")
        raise RuntimeError("Translation failed after 3 attempts.")

    def parallel_translate(self, chunks):
        with ThreadPool(8) as pool:
            return pool.map(self.translate_text, chunks)

    def chunk_text(self, texts):
        result, chunk = [], ''
        for text in texts:
            if len(chunk) + len(text) < self.max_trans_chars:
                chunk += text + '\n-----\n'
            else:
                result.append(chunk.strip())
                chunk = text + '\n-----\n'
        if chunk:
            result.append(chunk.strip())
        return result

    def extract_chunks(self, translated_chunks):
        return [item for chunk in translated_chunks for item in chunk.split('-----')]

    def repackage_epub(self):
        try:
            mimetype_path = Path(self.file_extracted_path) / 'mimetype'
            mimetype_path.write_text('application/epub+zip')
            output_file = f"{self.file_extracted_path}.epub"
            with zipfile.ZipFile(output_file, 'w') as archive:
                archive.write(str(mimetype_path), 'mimetype',
                              compress_type=zipfile.ZIP_STORED)
                for file in Path(self.file_extracted_path).rglob('*'):
                    if file.name == 'mimetype':
                        continue
                    archive.write(str(file), str(file.relative_to(
                        self.file_extracted_path)), compress_type=zipfile.ZIP_DEFLATED)
            shutil.rmtree(self.file_extracted_path)
            logging.info("Successfully created translated epub.")
        except Exception as e:
            logging.error(f"Failed to create epub: {e}")

    def run(self, file_path):
        self.get_epub_file_info(file_path)
        if self.extract_epub():
            self.get_epub_html_paths()
            self.multithread_translate_html()
            self.repackage_epub()


def main():
    parser = argparse.ArgumentParser(
        description='Translate EPUB files using Google Translate.')
    parser.add_argument('epub_file_path', type=str,
                        help='Path to the epub file')
    parser.add_argument('-l', '--lang', type=str,
                        help='Destination language code (e.g. "vi" for Vietnamese)', default='vi')
    parser.add_argument('-d', '--dict', type=str,
                        help='Path to the translation dictionary')
    parser.add_argument('-v', '--version', action='version',
                        version=f'epub-translator v{TOOL_VERSION}')
    args = parser.parse_args()

    epub_file = Path(args.epub_file_path).resolve()
    if not epub_file.is_file() or epub_file.suffix.lower() != '.epub':
        logging.error("Invalid epub file path.")
        sys.exit(1)

    engine = TranslatorEngine(dest_lang=args.lang, dict_path=args.dict)
    engine.check_for_tool_updates()
    engine.run(str(epub_file))


if __name__ == '__main__':
    main()
