# -*- coding: utf-8 -*-
import os
import pathlib
import typing

from argparse import ArgumentParser
from datetime import datetime
from glob import glob
from os.path import join

import sqlite3
from PyPDF2 import PdfFileReader, PdfFileWriter
from PyPDF2.generic import Destination

############################################################
## Constants
############################################################

CONTROL_OPTIONS = """CONTROL OPTIONS:
    b (back)    - previous chapter
    c (choose)  - choose to add to a paper
    d (down)    - down to inner chapters
    f (finish)  - save the paper and exit
    n (next)    - next chapter
    o (omit)    - omit this file
    q (quit)    - exit without save
    u (up)      - go to the upper chapter list
"""

DATABASE = 'library.db'
LIBRARY_DIR = 'library'
MAX_LEN = 5
PDF_LIBRARY = 'pdf_library'
START_LEVEL = 0

TAB = ' ' * 5
CHOOSE_INDEX_FOR = TAB + 'Choose #n-th book to {operation} : '

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
            f'UPDATE {PDF_LIBRARY} SET active = 0 '
            f'WHERE title = "{filename}";'
        )

    def insert_book(self, filename: str):
        self.__commit(
            f'INSERT INTO {PDF_LIBRARY} (title, active) '
            f'VALUES ("{filename}", 1);'
        )

    def list(self, *, extra_conditions=''):
        query = (
            f'SELECT * from {PDF_LIBRARY} '
            f'WHERE {extra_conditions if extra_conditions else "1=1"} '
            'ORDER BY title'
        )
        return [dict(row) for row in self.cursor.execute(query)]

    def migrate(self):
        self.__commit(
            f"""
            CREATE TABLE IF NOT EXISTS {PDF_LIBRARY} (
                title VARCHAR(256) PRIMARY KEY NOT NULL,
                current_place VARCHAR(256),
                active BOOLEAN
            )
            """
        )

    def update_current_place(self, filename: str, current_place: str):
        self.__commit(
            f'UPDATE {PDF_LIBRARY} SET current_place = "{current_place}" '
            f'WHERE title = "{filename}";'
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

    def __add_chapter(self, book: dict):
        self.state_list: typing.List[tuple] = []
        idx = 0
        reader = PdfFileReader(join(LIBRARY_DIR, book.get('title', '')), strict=False)
        outlines = reader.outlines

        if outlines:
            self.__move_to_current_place(START_LEVEL, outlines, book.get('current_place', ''))
            self.state_list = self.state_list or [(START_LEVEL, outlines, idx)]

            while True:
                current_level, outlines, idx = self.state_list[-1]
                current_outline = outlines[idx]
                chapter = self.__get_chapter_from_outline(current_outline)
                if isinstance(current_outline, Destination):
                    print(f'\nBook: {book.get("title")}')
                    print(f'[{current_level}] Chapter: {chapter}')
                    print(f'Number of pages: {self.__chapter_pages(reader, outlines, idx)}\n')
                    print(f'Already written: {self.__written_pages}\n')
                    print(CONTROL_OPTIONS)

                cmd = input("Choose an option: ")
                self.__perform(cmd, reader, outlines, idx)

                if cmd.startswith('c'):
                    self.__connector.update_current_place(book.get('title'), chapter)
                    self.__next(reader, outlines, idx)
                elif cmd.startswith('q') or cmd.startswith('o') or cmd.startswith('f'):
                    self.__soft_exit = cmd.startswith('q') or cmd.startswith('f')
                    break

    def __back(self, reader: PdfFileReader, outlines: list, idx: int):
        if len(self.state_list) > 1:
            self.state_list.pop()
            while True:
                _, outlines, idx = self.state_list[-1]
                if isinstance(outlines[idx], Destination):
                    break
                self.state_list.pop()

    def __chapter_pages(self, reader: PdfFileReader, outlines: list, idx: int):
        current_outline = outlines[idx]
        current_page = reader.getDestinationPageNumber(current_outline)
        for idx_ in range(idx + 1, len(outlines)):
            next_outline = outlines[idx_]
            if isinstance(next_outline, Destination):
                return reader.getDestinationPageNumber(next_outline) - current_page
        current_level, *_ = self.state_list[-1]

        if current_level != START_LEVEL:
            for state in reversed(self.state_list):
                previous_level, previous_outlines, previous_idx = state
                if previous_level < current_level:
                    for outline in previous_outlines[(previous_idx + 1):]:
                        if isinstance(outline, Destination):
                            chapter_pages = reader.getDestinationPageNumber(outline) - current_page
                            if chapter_pages > 0:
                                return reader.getDestinationPageNumber(outline) - current_page
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

    def __get_chapter_from_outline(self, outline: Destination) -> str:
        title = outline.get('/Title')
        if isinstance(title, bytes):
            title = title.decode()
        return title.replace('\x00', '')

    def __finish(self, reader: PdfFileReader, outlines: list, idx: int):
        self.__save()

    def make_new(self):
        library = self.__connector.list(extra_conditions='active = 1')
        for book in library:
            if self.__soft_exit:
                return
            self.__add_chapter(book)
        self.__save()

    def __move_to_current_place(self, current_level: int, outlines: list,
                                current_place: str) -> bool:
        found = False
        added_elements = 0

        if current_place:
            for idx, outline in enumerate(outlines):
                self.state_list.append((current_level, outlines, idx))
                added_elements += 1
                if isinstance(outline, Destination):
                    chapter = self.__get_chapter_from_outline(outline)
                    found = (chapter == current_place)
                elif self.__move_to_current_place(current_level + 1, outline, current_place):
                    found = True
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

    def __omit(self, reader: PdfFileReader, outlines: list, idx: int):
        pass

    def __perform(self, command: str, reader: PdfFileReader, outlines: list, idx: int):
        HANDLERS = {
            'b': self.__back,
            'c': self.__choose,
            'd': self.__down,
            'f': self.__finish,
            'n': self.__next,
            'o': self.__omit,
            'q': self.__quit,
            'u': self.__up,
        }

        for key in HANDLERS:
            if command.startswith(key):
                handler = HANDLERS[key]
                handler(reader, outlines, idx)

    def __quit(self, reader: PdfFileReader, outlines: list, idx: int):
        pass

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
## Switch controller for all operations with library
############################################################

class Controller:

    def __init__(self):
        self.__connector = DBConnector()

    def create_new_paper(self):
        Paper(self.__connector).make_new()

    def exec_operation(self, operation: str):
        callback = {
            'add': self.__connector.insert_book,
            'delete': self.__connector.delete_book,
            'list': self.__list,
        }[operation]

        library = self.__list(operation)
        self.__print_library(library)

        if operation != 'list' and library:
            index_str = input(CHOOSE_INDEX_FOR.format(operation=operation)).strip()
            index = int(index_str) if index_str.isdigit() else 0
            if 0 < index <= len(library):
                callback(library[index - 1]['title'])
                self.exec_operation(operation)

    def __list(self, operation: str) -> typing.List[dict]:
        if operation == 'add':
            library = set(os.path.basename(book) for book in glob('library/*.pdf'))
            current_library = set(row['title'] for row in self.__connector.list())
            return [
                {'title': book, 'active': 0}
                for book in library.difference(current_library)
            ]

        extra_conditions = {
            'delete': 'active = 1',
            'list': 'active = 1',
        }[operation]
        return self.__connector.list(extra_conditions=extra_conditions)

    def __print_library(self, library: typing.List[dict]):
        library_str = 'Library is empty'
        if library:
            title_max_len = max([len(row['title']) for row in library])
            library_str = f'\n{TAB}{"title".ljust(title_max_len, " ")}{TAB}active\n'
            library_str += TAB + '-' * (len(library_str) - 12 - MAX_LEN) + TAB + '-' * 6 + '\n'
            for idx, row in enumerate(library):
                library_str += (
                    f'{(str(idx + 1) + ")").ljust(MAX_LEN)}'
                    f'{row["title"].ljust(title_max_len, " ")}'
                    f'{TAB}  {row["active"]}\n'
                )
        print(library_str)


############################################################
## Main
############################################################

def init():
    pathlib.Path(LIBRARY_DIR).mkdir(exist_ok=True)
    pathlib.Path(DATABASE).touch(exist_ok=True)
    DBConnector().migrate()


if __name__ == '__main__':
    init()
    parser = ArgumentParser(description='Options for pdf paper slicing and merging')
    parser.add_argument(
        '-add', dest='add', action='store_const', const=True,
        help='Add new book to current library'
    )
    parser.add_argument(
        '-list', dest='list', action='store_const', const=True,
        help='List current library'
    )
    parser.add_argument(
        '-paper', dest='paper', action='store_const', const=True,
        help='Create new paper to read'
    )
    parser.add_argument(
        '-remove', dest='remove', action='store_const', const=True,
        help='Remove book from database'
    )

    args = vars(parser.parse_args())
    controller = Controller()

    if args['list']:
        controller.exec_operation('list')
    elif args['add']:
        controller.exec_operation('add')
    elif args['remove']:
        controller.exec_operation('delete')
    elif args['paper']:
        controller.create_new_paper()
