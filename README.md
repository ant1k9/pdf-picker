## Pdf Picker

This scripts helps you to reorganize your PDF library.

You can choose any chapters from different books to compile them in one PDF file.
This will be helpful to make a book or a journal from different sources.

Limitation is that every book in your library should have outlines.

You can run the script and choose different control options.

If you iterate through your library or choose the **finish** command, then in the current folder a new compiled file will be created.

### Usage

```bash
>>> python pdf_picker.py --help
>>> python pdf_picker.py -add     # adds file from library/ path to the iterating collection
>>> python pdf_picker.py -list    # list all active books to pick chapters
>>> python pdf_picker.py -paper   # creates new PDF paper by your control
>>> python pdf_picker.py -remove  # remove book from the iterating collection
```

### Control Options

<pre>
    b (back)    - previous chapter
    c (choose)  - choose to add to a paper
    d (down)    - down to inner chapters
    f (finish)  - save the paper and exit
    n (next)    - next chapter
    o (omit)    - omit this file
    q (quit)    - exit without save
    u (up)      - go to the upper chapter list
</pre>

### TODO
