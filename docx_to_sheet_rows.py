"""
docx_to_sheet_rows.py
----------------------
Converts an MCQ test-series .docx (built in our established Question/
Type/Option/Solution/Marks table format) into a CSV matching the
Telegram Quiz Scheduler's sheet_template.csv columns, so you can paste
the output straight into the Google Sheet instead of retyping
questions by hand.

Usage:
    python docx_to_sheet_rows.py input.docx output.csv

Notes on what it does automatically:
  - If a question/option is bilingual (English/Hindi combined, as our
    test-series builder writes it, e.g. "Adopted/ गोद लिए हुए"), this
    script keeps only the English part, since Telegram's native quiz
    UI is a single short field and mixing both languages usually blows
    past the character limit.
  - If a question has an explanation written under "Solution", the
    first explanation line (English) is carried over into the
    Explanation column; anything past 200 characters is trimmed to fit
    Telegram's limit (with a note printed to the terminal so you know
    which rows were shortened).
  - Date and Time columns are left BLANK on purpose -- you choose when
    each quiz should go out by filling those in yourself in the sheet.
  - Posted is set to "No" for every row.
  - Questions with more than one "correct" option (rare, only from
    papers where the source answer key itself listed two correct
    answers) are flagged and skipped, since Telegram's native quiz
    format only supports a single correct answer -- these need a
    manual decision from you about which one to keep.
"""

import sys
import csv
import re
from docx import Document

EXPLANATION_MAX = 200


def english_part(text):
    """Return the English half of a bilingual 'English/ Hindi' string,
    or the whole string unchanged if it isn't in that format."""
    if "/ " in text:
        return text.split("/ ", 1)[0].strip()
    return text.strip()


def first_line(text):
    return text.split("\n")[0].strip()


def explanation_from_solution(text):
    """Solution cell text looks like 'Answer: ...\\nEnglish explanation\\nHindi explanation'
    (or just 'Answer: ...' with no explanation at all). Returns the
    English explanation line, or '' if there isn't one."""
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) > 1:
        return lines[1].strip()
    return ""


def parse_table(table, table_num):
    rows = table.rows
    question_text = english_part(first_line(rows[0].cells[1].text))

    options = []
    correct_indices = []
    solution_text = ""
    i = 2  # row 0 = Question, row 1 = Type, options start at row 2
    while i < len(rows):
        label = rows[i].cells[0].text.strip()
        if label == "Option":
            opt_text = english_part(rows[i].cells[1].text)
            tag = rows[i].cells[2].text.strip().lower()
            options.append(opt_text)
            if tag == "correct":
                correct_indices.append(len(options) - 1)
            i += 1
        elif label == "Solution":
            solution_text = rows[i].cells[1].text
            i += 1
        else:
            i += 1  # Marks row or anything else, skip

    return {
        "table_num": table_num,
        "question": question_text,
        "options": options,
        "correct_indices": correct_indices,
        "explanation": explanation_from_solution(solution_text),
    }


def main():
    if len(sys.argv) != 3:
        print("Usage: python docx_to_sheet_rows.py input.docx output.csv")
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    doc = Document(in_path)

    written = 0
    skipped_multi_correct = 0
    skipped_wrong_option_count = 0
    trimmed_explanations = 0

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Date", "Time", "Question", "Option A", "Option B", "Option C",
             "Option D", "Correct Option", "Explanation", "Posted"]
        )

        for idx, table in enumerate(doc.tables, start=1):
            parsed = parse_table(table, idx)

            if len(parsed["options"]) != 4:
                print(f"Question {idx}: has {len(parsed['options'])} options, "
                      f"not 4 -- skipped (sheet template expects exactly 4).")
                skipped_wrong_option_count += 1
                continue

            if len(parsed["correct_indices"]) != 1:
                print(f"Question {idx}: has {len(parsed['correct_indices'])} correct "
                      f"answers marked -- skipped, needs a manual decision.")
                skipped_multi_correct += 1
                continue

            correct_letter = "ABCD"[parsed["correct_indices"][0]]

            explanation = parsed["explanation"]
            if len(explanation) > EXPLANATION_MAX:
                explanation = explanation[:EXPLANATION_MAX]
                trimmed_explanations += 1

            writer.writerow(
                ["", "", parsed["question"], *parsed["options"],
                 correct_letter, explanation, "No"]
            )
            written += 1

    print(f"\nDone: {written} rows written to {out_path}")
    if skipped_multi_correct:
        print(f"  {skipped_multi_correct} question(s) skipped: more than one correct answer marked.")
    if skipped_wrong_option_count:
        print(f"  {skipped_wrong_option_count} question(s) skipped: not exactly 4 options.")
    if trimmed_explanations:
        print(f"  {trimmed_explanations} explanation(s) trimmed to fit Telegram's 200-character limit.")
    print("\nOpen the CSV, fill in Date and Time for each row, then paste it into your Google Sheet.")


if __name__ == "__main__":
    main()
