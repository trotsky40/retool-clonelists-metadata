#!/usr/bin/env python

import json
import json_source_map # type: ignore
import jsonpath_ng # type: ignore
import jsonschema
import pathlib
import re
import subprocess
import sys
import traceback

from typing import Any

def main() -> None:
    # Get uncommited Git changes
    files = subprocess.run('git diff --name-only', stdout=subprocess.PIPE).stdout.decode('utf-8').split('\n')
    files = [x for x in files if 'clonelists' in x]

    if not files:
        # Compare current commit and previous commit to get files that have changed
        if sys.platform.startswith('win'):
            files = subprocess.run('git diff HEAD~ HEAD --name-only', stdout=subprocess.PIPE).stdout.decode('utf-8').split('\n')
        else:
            files = subprocess.run('git diff HEAD^ HEAD --name-only', stdout=subprocess.PIPE).stdout.decode('utf-8').split('\n')

    files = [x for x in files if 'clonelists' in x]

    for file in files:
        if file != 'hash.json':
            print(f'\n\nValidating {file}\n{'-----------'}{'-'*len(file)}\n')

            error_messages: dict[int, dict[str, Any]] = {}
            clonelist: Any
            cloneliststr: str = ''

            try:
                with open(pathlib.Path(file), 'r', encoding='utf-8') as clone_list_file:
                    clonelist = json.load(clone_list_file)
                    # Read the clone list as a string. This is required to find the line number later
                    # for JSON schema validation errors.
                    clone_list_file.seek(0)
                    cloneliststr = clone_list_file.read()
            except json.decoder.JSONDecodeError as e:
                if e.lineno not in error_messages:
                    error_messages[e.lineno] = {}

                if 'comment' not in error_messages[e.lineno]:
                    error_messages[e.lineno]['comment'] = ''

                # TODO GitHub should post this in some way
                error_messages[e.lineno]['comment'] = (
                    f'Invalid JSON found on or before this line ({e.lineno}). Fix the error '
                    'to continue.\n\nThere might be more invalid JSON in this file, but only '
                    'one line can be checked for at a time. To speed up error checking, try '
                    'an [online JSON validator](https://jsonlint.com/), or use an IDE that '
                    'can lint JSON like [Visual Studio code](https://code.visualstudio.com/) '
                    'to find errors before updating your PR.')
                print(error_messages)

                sys.exit()
            except Exception as e:
                print(f'Unexpected error reading JSON file: {e}')
                sys.exit()

            # Load the JSON schema
            with open(pathlib.Path('tests/clone-list-schema.json'), 'r', encoding='utf-8') as schema_file:
                schema = json.load(schema_file)

            validator = jsonschema.Draft202012Validator(schema)

            # Get all schema validation errors
            errors = validator.iter_errors(clonelist)
            parent_comments: list[str] = []

            for error in errors:
                error_path = error.json_path.replace('.', '/').replace('$', '').replace('[', '/').replace(']', '')
                comment: str = ''
                parent_comment: str = ''
                local_names_str: str = ''
                region_names_str: str = ''

                # If $ref is used in a schema, then we have to pull the appropriate parent
                # $comment instead of the $comment in the $ref.
                parent_comment_json_path = f'{re.sub("\\[[0-9]+\\]", "", error.json_path).replace('.', "..")}["$comment"]'

                if '$..variants..titles..filters..' in parent_comment_json_path:
                    parent_comment_json_path = parent_comment_json_path.replace('..variants..titles', '')

                # Extract the comment value
                jsonpath_expr = jsonpath_ng.parse(parent_comment_json_path)
                parent_comments = [match.value for match in jsonpath_expr.find(schema)]

                if parent_comments:
                    parent_comment = parent_comments[0]

                if '$comment' in error.schema:
                    comment = error.schema['$comment']
                elif parent_comment:
                    comment = parent_comment

                # Find the line in the JSON where the error took place
                source_map = json_source_map.calculate(cloneliststr)
                error_line = source_map[error_path].value_start.line + 1

                # Populate the error_messages dict
                if error_line not in error_messages:
                    error_messages[error_line] = {}

                # Add comments for GitHub
                if 'comment' not in error_messages[error_line]:
                    error_messages[error_line]['comment'] = comment
                else:
                    if error_messages[error_line]['comment'] != comment:
                        error_messages[error_line]['comment'] = f'{error_messages[error_line]["comment"]}\n\n{comment}'

                # Pull languages out of the appropriate $ref if localNames is being queried
                if 'localNames' in error.json_path:
                    local_names_expr = jsonpath_ng.parse('$..languages..properties')
                    local_names = [match.value for match in local_names_expr.find(schema)]

                    if local_names:
                        local_names_str = '`, `'.join([x for x in [match.value for match in local_names_expr.find(schema)][0].keys()])

                if local_names_str:
                    error_messages[error_line]['comment'] = f'{error_messages[error_line]["comment"]}\n\nThe valid languages are as follows:\n\n`{local_names_str}`'

                # Pull regions out of the appropriate $ref if matchRegions is being queried
                if (
                    'matchRegions' in error.json_path
                    or 'higherRegions' in error.json_path
                    or 'lowerRegions' in error.json_path
                    ):
                    region_names_expr = jsonpath_ng.parse('$..regions..enum')
                    regions = [match.value for match in region_names_expr.find(schema)]

                    if regions:
                        region_names_str = '`, `'.join(regions[0])

                if region_names_str:
                    error_messages[error_line]['comment'] = f'{error_messages[error_line]["comment"]}\n\nThe valid regions are as follows:\n\n`{region_names_str}`'

                # Add the JSON schema error messages
                if 'errors' not in error_messages[error_line]:
                    error_messages[error_line]['errors'] = []

                error_messages[error_line]['errors'].append(error.message)

            # TODO GitHub should post this in some way
            if error_messages:
                print(error_messages)

            # Check for duplicate titles.searchTerm values
            jsonpath_expr = jsonpath_ng.parse('$..titles..searchTerm')
            titles_searchterms = [match.value for match in jsonpath_expr.find(clonelist)]
            seen: set[str] = set()
            dupes: set[str] = set()

            for titles_searchterm in titles_searchterms:
                if titles_searchterm not in seen:
                    seen.add(titles_searchterm)
                else:
                    dupes.add(titles_searchterm)

            searchterm_dupes: dict[str, list[int]] = {}

            for i, line in enumerate(cloneliststr.split('\n'), start=1):
                for dupe in dupes:
                    if f'{{"searchTerm": "{dupe}"' in line:
                        if dupe not in searchterm_dupes:
                            searchterm_dupes[dupe] = []

                        searchterm_dupes[dupe].append(i)

            for searchterm_name, searchterm_lines in searchterm_dupes.items():
                comment_line: int = searchterm_lines[0]
                # TODO GitHub should post this in some way, using comment_line
                print(f'Found the search term `{searchterm_name}` multiple times on the following lines:\n\n{'\n'.join(str(x) for x in searchterm_lines)}\n\nSearch terms for `titles` should only be associated with one `group`, and not be repeated within that `group`.')

            # Check that groups aren't listed more than once
            jsonpath_expr = jsonpath_ng.parse('$..variants[*].group')
            groups = [match.value for match in jsonpath_expr.find(clonelist)]

            seen = set()
            dupes = set()

            for group in groups:
                if group not in seen:
                    seen.add(group)
                else:
                    dupes.add(group)

            group_dupes: dict[str, list[int]] = {}

            for i, line in enumerate(cloneliststr.split('\n'), start=1):
                for dupe in dupes:
                    if f'"group": "{dupe}"' in line:
                        if dupe not in group_dupes:
                            group_dupes[dupe] = []

                        group_dupes[dupe].append(i)

            for group_name, group_lines in group_dupes.items():
                comment_line = searchterm_lines[0]
                # TODO GitHub should post this in some way, using comment_line
                print(f'Found the group `{group_name}` multiple times on the following lines:\n\n{'\n'.join(str(x) for x in group_lines)}\n\nThere should only be one instance of a `group` name in a `variants` array.')

            if not error_messages and not group_dupes and not searchterm_dupes:
                print('No problems found.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('\n• Unexpected error:\n\n')
        traceback.print_exc()