"""Create a portable SQL backup of the configured SAHAL MySQL database."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()

    database_url = dotenv_values('.env').get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL is required in .env.')

    engine = create_engine(database_url)
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute('SHOW FULL TABLES WHERE Table_type = "BASE TABLE"')
        tables = [row[0] for row in cursor.fetchall()]

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('w', encoding='utf-8', newline='\n') as output:
            output.write('SET FOREIGN_KEY_CHECKS=0;\n')
            for table in tables:
                cursor.execute(f'SHOW CREATE TABLE `{table}`')
                output.write(f'DROP TABLE IF EXISTS `{table}`;\n')
                output.write(f'{cursor.fetchone()[1]};\n\n')

                cursor.execute(f'SELECT * FROM `{table}`')
                columns = [f'`{column[0]}`' for column in cursor.description]
                rows = cursor.fetchall()
                if rows:
                    output.write(f'INSERT INTO `{table}` ({", ".join(columns)}) VALUES\n')
                    serialized_rows = []
                    for row in rows:
                        serialized_rows.append(
                            '(' + ', '.join(raw_connection.escape(value) for value in row) + ')'
                        )
                    output.write(',\n'.join(serialized_rows))
                    output.write(';\n\n')
            output.write('SET FOREIGN_KEY_CHECKS=1;\n')
    finally:
        raw_connection.close()
        engine.dispose()


if __name__ == '__main__':
    main()
