# -*- coding: utf-8 -*-
import pathlib
import random
import sys
import typing

from datetime import datetime
from os.path import join

import sqlite3
from PyPDF2 import PdfFileReader, PdfFileWriter
from PyPDF2.generic import Destination

############################################################
## Constants
############################################################

DATABASE = 'library_everyday.db'
LIBRARY_DIR = 'library'
MAX_LEN = 5
PDF_LIBRARY = 'pdf_library'
START_LEVEL = 0
SOFT_LIMIT = 20
HARD_LIMIT = 40

############################################################
## Database Connector
############################################################


class DBConnector:

    def __init__(self):
        self.connection = sqlite3.Connection(DATABASE)
        self.connection.row_factory = sqlite3.Row
        self.cursor = self.connection.cursor()

    def __commit(self, command: str):
        self.cursor.execute(command)
        self.connection.commit()

    def delete_book(self, filename: str):
        self.__commit(
            f"UPDATE {PDF_LIBRARY} SET active = 0 "
            f"WHERE title = '{filename}';"
        )

    def insert_book(self, filename: str, topic: str):
        self.__commit(
            f"INSERT INTO {PDF_LIBRARY} (title, topic, active) "
            f"VALUES ('{filename}', '{topic}', 1);"
        )

    def list(self, *, extra_conditions=''):
        query = (
            f'SELECT * from {PDF_LIBRARY} '
            f'WHERE {extra_conditions if extra_conditions else "1=1"} '
            'ORDER BY title'
        )
        return [dict(row) for row in self.cursor.execute(query)]

    def topics(self):
        return [
            row['topic'] for row in self.cursor.execute(
                f'SELECT DISTINCT topic FROM {PDF_LIBRARY} WHERE active'
            )
        ]

    def migrate(self):
        self.__commit(
            f"""
            CREATE TABLE IF NOT EXISTS {PDF_LIBRARY} (
                title VARCHAR(256) PRIMARY KEY NOT NULL,
                topic VARCHAR(32) NOT NULL,
                current_place VARCHAR(256),
                active BOOLEAN
            );
            """
        )

    def update_current_place(self, filename: str, current_place: str):
        self.__commit(
            f"UPDATE {PDF_LIBRARY} SET current_place = '{current_place}' "
            f"WHERE title = '{filename}';"
        )


############################################################
## Pdf iterator, combiner and linker
############################################################

class Paper:

    def __init__(self, connector):
        self.__connector = connector
        self.__soft_exit = False
        self.__writer = PdfFileWriter()
        self.__written_pages = 0

    def __accumulate_pages(self, reader: PdfFileReader, book: dict):
        idx = 0
        collected_pages = 0

        while True:
            current_level, outlines, idx = self.state_list[-1]
            current_outline = outlines[idx]
            chapter = self.__get_chapter_from_outline(current_outline)

            if isinstance(current_outline, Destination):
                chapter_pages = self.__chapter_pages(reader, outlines, idx)
                hash_size = len(self.state_list)

                if chapter_pages + collected_pages > HARD_LIMIT \
                        and self.__go_down_for_small_chapter(reader, outlines, idx, hash_size):
                    continue

                self.__choose(reader, outlines, idx)
                collected_pages += chapter_pages
                self.__next(reader, outlines, idx)

                if collected_pages >= SOFT_LIMIT or hash_size == len(self.state_list):
                    if not self.__find_next_place_to_read(reader, outlines, idx, hash_size):
                        self.__connector.delete_book(book.get('title'))
                    _, outlines, idx = self.state_list[-1]
                    chapter = self.__get_chapter_from_outline(outlines[idx])
                    self.__connector.update_current_place(book.get('title'), chapter)
                    break

    def __add_chapter(self, book: dict):
        self.state_list: typing.List[tuple] = []
        reader = PdfFileReader(join(LIBRARY_DIR, book.get('title', '')), strict=False)
        outlines = reader.outlines
        idx = 0

        if outlines:
            self.__move_to_current_place(START_LEVEL, outlines, book.get('current_place', ''))
            self.state_list = self.state_list or [(START_LEVEL, outlines, idx)]
            self.__accumulate_pages(reader, book)

    def __back(self, reader: PdfFileReader, outlines: list, idx: int):
        if len(self.state_list) > 1:
            self.state_list.pop()
            while True:
                _, outlines, idx = self.state_list[-1]
                if isinstance(outlines[idx], Destination):
                    break
                self.state_list.pop()

    def __chapter_pages(self, reader: PdfFileReader, outlines: list, idx: int) -> int:
        current_outline = outlines[idx]
        current_page = reader.getDestinationPageNumber(current_outline)
        for idx_ in range(idx + 1, len(outlines)):
            next_outline = outlines[idx_]
            if isinstance(next_outline, Destination):
                return reader.getDestinationPageNumber(next_outline) - current_page

        current_level, *_ = self.state_list[-1]
        if current_level != START_LEVEL:
            pages_to_upper_chapter = self.__pages_to_next_upper_chapter(
                reader, current_page, current_level
            )
            if pages_to_upper_chapter > 0:
                return pages_to_upper_chapter

        return reader.numPages - current_page

    def __choose(self, reader: PdfFileReader, outlines: list, idx: int):
        current_outline = outlines[idx]
        pages = self.__chapter_pages(reader, outlines, idx)
        current_page = reader.getDestinationPageNumber(current_outline)
        for page in range(current_page, current_page + pages):
            self.__writer.addPage(reader.getPage(page))
            self.__written_pages += 1

    def __down(self, reader: PdfFileReader, outlines: list, idx: int):
        if idx < len(outlines) - 1:
            next_outline = outlines[idx + 1]
            if isinstance(next_outline, list):
                current_level, *_ = self.state_list[-1]
                self.state_list.append((current_level + 1, next_outline, 0))

    def __find_next_place_to_read(
            self, reader: PdfFileReader, outlines: list, idx: int, hash_size: int) -> bool:
        for _ in range(5):
            if len(self.state_list) == hash_size:
                if isinstance(outlines[idx], Destination) \
                        and self.__is_the_end(reader, outlines, idx):
                    continue
                self.__up(reader, outlines, idx)
                hash_size = len(self.state_list)
                _, outlines, idx = self.state_list[-1]
                self.__next(reader, outlines, idx)
            else:
                return True
        else:
            return False

    def __get_chapter_from_outline(self, outline: Destination) -> str:
        title = outline.get('/Title')
        if isinstance(title, bytes):
            title = title.decode()
        return title.replace('\x00', '')

    def __go_down_for_small_chapter(
            self, reader: PdfFileReader, outlines: list, idx: int, hash_size: int) -> bool:
        self.__down(reader, outlines, idx)
        if len(self.state_list) != hash_size:
            return True
        return False

    def __is_the_end(self, reader: PdfFileReader, outlines: list, idx: int) -> bool:
        left_pages = reader.numPages - reader.getDestinationPageNumber(outlines[idx])
        return self.__chapter_pages(reader, outlines, idx) == left_pages

    def make_new(self, book: dict):
        self.__add_chapter(book)
        self.__save()

    def __move_to_current_place(self, current_level: int, outlines: list, chapter: str) -> bool:
        found = False
        added_elements = 0

        if chapter:
            for idx, outline in enumerate(outlines):
                self.state_list.append((current_level, outlines, idx))
                added_elements += 1
                if isinstance(outline, Destination):
                    found = (self.__get_chapter_from_outline(outline) == chapter)
                else:
                    found = found or (
                        self.__move_to_current_place(current_level + 1, outline, chapter)
                    )
                if found:
                    break

            if not found and added_elements:
                self.state_list = self.state_list[:-added_elements]
        return found

    def __next(self, reader: PdfFileReader, outlines: list, idx: int):
        for idx_ in range(idx + 1, len(outlines)):
            next_outline = outlines[idx_]
            if isinstance(next_outline, Destination):
                current_level, *_ = self.state_list[-1]
                self.state_list.append((current_level, outlines, idx_))
                return

    def __pages_to_next_upper_chapter(
            self, reader: PdfFileReader, current_page: int, current_level: int) -> int:
        for state in reversed(self.state_list):
            previous_level, previous_outlines, previous_idx = state
            if previous_level < current_level:
                for outline in previous_outlines[(previous_idx + 1):]:
                    if isinstance(outline, Destination):
                        chapter_pages = reader.getDestinationPageNumber(outline) - current_page
                        if chapter_pages > 0:
                            return chapter_pages
        return 0

    def __save(self):
        current_date_prefix = datetime.strftime(datetime.now(), '%Y%m%d_%H%M%S')
        with open(f'{current_date_prefix}_paper.pdf', 'wb') as wfile:
            self.__writer.write(wfile)

    def __up(self, reader: PdfFileReader, outlines: list, idx: int):
        reference_level, *_ = self.state_list[-1]
        while len(self.state_list) > 1:
            current_level, *_ = self.state_list[-1]
            if current_level < reference_level:
                break
            self.state_list.pop()


############################################################
## Main
############################################################

def main():
    pathlib.Path(LIBRARY_DIR).mkdir(exist_ok=True)
    pathlib.Path(DATABASE).touch(exist_ok=True)
    connector = DBConnector()
    connector.migrate()

    existing_books = set(row['title'] for row in connector.list())
    for book in pathlib.Path(LIBRARY_DIR).iterdir():
        if book.is_file() and book.name not in existing_books:
            topic = input(f'Choose a topic for book "{book}": ')
            connector.insert_book(book.name, topic)

    try:
        book = random.choice(connector.list(
            extra_conditions=f"active = 1 AND topic = '{sys.argv[1]}'"
        ))
        Paper(connector).make_new(book)
    except IndexError:
        print(
            'Choose an existing topic for a paper:\n\033[1m   ' +
            '\n   '.join(connector.topics()) +
            '\033[0m'
        )


if __name__ == '__main__':
    main()
