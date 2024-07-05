#!/usr/bin/python
# -*- coding: utf-8 -*-
import os
import argparse

# basicamente, só precisamos manter db.json, cursos.json e campi.json, o resto é supérfluo.
# então esse programa apaga tudo que não são eles.
# O gpt fez esse programa:

def main(directory):
    # Define a lista dos arquivos que devem permanecer
    keep_files = {'cursos.json', 'campi.json', 'db.json', 'cursos.json.gz', 'campi.json.gz', 'db.json.gz'}

    # Lista todos os arquivos no diretório especificado
    all_files = set(os.listdir(directory))

    # Calcula os arquivos para remover (todos exceto os que devem ser mantidos)
    to_remove = all_files - keep_files

    # Remove os arquivos que não estão na lista de manutenção
    for file in to_remove:
        file_path = os.path.join(directory, file)
        os.remove(file_path)
        print(f"Removed: {file_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cleanup utility for MatrUSP data")
    parser.add_argument('directory', help="Directory containing the data files to cleanup")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print("Specified directory does not exist.")
        parser.print_help()
        exit(1)

    main(args.directory)
