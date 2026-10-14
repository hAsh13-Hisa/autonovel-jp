import re
import os
import argparse
from pathlib import Path

def half_to_full(s):
    """Convert half-width alphanumeric to full-width."""
    res = ""
    for char in s:
        cp = ord(char)
        if 48 <= cp <= 57: # 0-9
            res += chr(cp + 65248)
        elif 65 <= cp <= 90: # A-Z
            res += chr(cp + 65248)
        elif 97 <= cp <= 122: # a-z
            res += chr(cp + 65248)
        else:
            res += char
    return res

def conversion_callback(match):
    s = match.group(0)
    # 1-3 characters are converted to full-width for vertical writing compatibility
    if len(s) <= 3:
        return half_to_full(s)
    return s


# 章番号の数字まで全角にしないための免除。
# なぜ必要か（実測 2026-10-14）: 「# 第1章：錆びた澱の静寂」の 1 まで
# 全角の「１」に変換され、見出しが「第１章」になった。本文中の「第2章」も同様。
# 縦書きでも章番号は半角のままで問題ない（むしろ半角のほうが他ツールと一致する）。
HEADING_NUM = re.compile(r'(#{1,6}\s*第\d+章|第\d+章)')

def process_directory(directory):
    pattern = re.compile(r'[a-zA-Z0-9]+')
    total_replacements = 0
    files_processed = 0

    path = Path(directory)
    for file_path in path.glob('*.md'):
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()

        # 章見出しの数字は全角にしない（実測 2026-10-14 で「第1章」が「第１章」に
        # なっていた）。→ 見出しを一時的に避難させてから変換し、あとで戻す。
        heads = []

        def _stash(m):
            heads.append(m.group(0))
            return f"\x00{len(heads) - 1}\x00"

        text = HEADING_NUM.sub(_stash, text)

        matches = pattern.findall(text)
        replaced_count = sum(1 for m in matches if len(m) <= 3)

        if replaced_count > 0:
            new_text = pattern.sub(conversion_callback, text)
            # 避難した見出しを戻す
            new_text = re.sub(r'\x00(\d+)\x00',
                              lambda m: heads[int(m.group(1))], new_text)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_text)
            total_replacements += replaced_count
            print(f"Converted {replaced_count} occurrences in {file_path.name}")

        files_processed += 1

    print(f"\nProcessed {files_processed} files.")
    print(f"Total replacements: {total_replacements}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert short half-width alphanumeric to full-width for vertical writing.")
    parser.add_argument("directory", help="Directory containing markdown chapters")
    args = parser.parse_args()
    
    process_directory(args.directory)
