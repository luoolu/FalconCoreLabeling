#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 6/25/25
# @Author  : luoolu
# @Github  : https://luoolu.github.io
# @Software: PyCharm
# @File    : txt_labels2json.py
import json

# Define file paths
file_paths = [
    '/mnt/data/碳酸盐岩label.txt',
    '/mnt/data/砂岩label.txt',
    '/mnt/data/火山碎屑岩label.txt',
    '/mnt/data/岩浆岩label.txt',
]

labels_dict = {}

# Process each file
for path in file_paths:
    with open(path, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]
    # First line is the category name
    category = lines[0]
    # Extract labels from subsequent lines starting with '- '
    labels = []
    for line in lines[1:]:
        if line.startswith('- '):
            label = line[2:].strip()
            # Remove surrounding quotes if present
            if label.startswith('"') and label.endswith('"'):
                label = label[1:-1]
            labels.append(label)
    labels_dict[category] = labels

# Write to JSON file
output_path = 'labels.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(labels_dict, f, ensure_ascii=False, indent=2)

print(f'Labels JSON saved to {output_path}')
