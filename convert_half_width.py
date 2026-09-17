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

def process_directory(directory):
    pattern = re.compile(r'[a-zA-Z0-9]+')
    total_replacements = 0
    files_processed = 0

    path = Path(directory)
    for file_path in path.glob('*.md'):
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
        
        matches = pattern.findall(text)
        replaced_count = sum(1 for m in matches if len(m) <= 3)
        
        if replaced_count > 0:
            new_text = pattern.sub(conversion_callback, text)
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
