#!/usr/bin/env python

import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from time import gmtime, sleep, strftime
from typing import Any

import json_source_map  # type: ignore
import jsonpath_ng  # type: ignore
import jsonschema  # type: ignore
import requests  # type: ignore


def add_comment(
    timeout: int,
    personal_access_token: str | None,
    pr_number: str | None,
    commit_id: str | None,
    filepath: str,
    pr_comment: str,
    line_number: int = 0,
    dupe_check: bool = False,
) -> int:
    headers: dict[str, str] = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {personal_access_token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    data: dict[str, int | str] = {}

    if line_number == 0:
        data = {
            'body': f'{pr_comment}',
            'commit_id': f'{commit_id}',
            'path': f'{filepath}',
            'side': 'RIGHT',
            'subject_type': 'file',
        }
    else:
        data = {
            'body': f'{pr_comment}',
            'commit_id': f'{commit_id}',
            'line': line_number,
            'path': f'{filepath}',
            'side': 'RIGHT',
        }

    try:
        print(data)

        comment_post = requests.post(
            f'https://api.github.com/repos/unexpectedpanda/retool-clonelists-metadata/pulls/{pr_number}/comments',
            headers=headers,
            json=data,
        )

        print(f'{comment_post.status_code} | {comment_post.reason}')
        print(json.dumps(comment_post.content.decode('utf-8'), indent=2))
        print('=========== END COMMENT ===========')

        # Catch checks for duplicate searchTerms or groups, which have to iterate through
        # multiple lines
        if dupe_check:
            return comment_post.status_code # type: ignore

        comment_post.raise_for_status()
    except requests.exceptions.Timeout:
        request_retry(
            add_comment,
            timeout=timeout,
            personal_access_token=personal_access_token,
            pr_number=pr_number,
            commit_id=commit_id,
            filepath=filepath,
            pr_comment=pr_comment,
            line_number=line_number,
        )
    except requests.ConnectionError:
        request_retry(
            add_comment,
            timeout=timeout,
            personal_access_token=personal_access_token,
            pr_number=pr_number,
            commit_id=commit_id,
            filepath=filepath,
            pr_comment=pr_comment,
            line_number=line_number,
        )
    except requests.exceptions.HTTPError as e:
        print('HTTP error triggered')

        if e.response.status_code == 401:
            print(f'Unauthorized access (401): {e}')
            sys.exit(1)
        elif e.response.status_code == 404:
            print(f'URL not found (404): {e}')
            sys.exit(1)
        elif e.response.status_code == 422:
            print(f'Unprocessable content (422): {e}')
            # Most likely this error is caused by trying to post to an unchanged line.
            # Attach the comment to the file instead.
            if line_number == 0:
                # Already attempted to comment on file
                print('Commenting on file failed.')
            else:
                print('Attempting to comment on file...')
                request_retry(
                    add_comment,
                    timeout=timeout,
                    personal_access_token=personal_access_token,
                    pr_number=pr_number,
                    commit_id=commit_id,
                    filepath=filepath,
                    pr_comment=pr_comment,
                    line_number=0,
                )
        elif e.response.status_code == 429:
            print(f'Rate limited (429): {e}')
            request_retry(
                add_comment,
                timeout=timeout,
                personal_access_token=personal_access_token,
                pr_number=pr_number,
                commit_id=commit_id,
                filepath=filepath,
                pr_comment=pr_comment,
                line_number=line_number,
            )
        elif str(e.response.status_code).startswith('5'):
            print(f'Server side error ({e.response.status_code}): {e}')
            request_retry(
                add_comment,
                timeout=timeout,
                personal_access_token=personal_access_token,
                pr_number=pr_number,
                commit_id=commit_id,
                filepath=filepath,
                pr_comment=pr_comment,
                line_number=line_number,
            )
    except Exception as e:
        print(e)
        sys.exit(1)

    return comment_post.status_code  # type: ignore


def request_retry(func: Any, **kwargs: Any) -> None:
    """
    Retries a request if a timeout has occurred.

    Args:
        func(): The API request function to call.

        kwargs (str): Additional keyword arguments.

    Returns:
        requests.models.Response: The response from the MobyGames API.
    """
    # Progressively increase the timeout with each retry
    progressive_timeout: list[int] = [0, 60, 300, -1]

    # Set an empty response with a mock error code
    response: requests.models.Response = requests.models.Response()
    response.status_code = 418

    while response.status_code != 200:
        if progressive_timeout[kwargs['timeout']] == -1:
            print('Too many retries, exiting...')
            sys.exit(1)
        else:
            message_printed: bool = False
            for j in range(progressive_timeout[kwargs['timeout']]):
                if not message_printed:
                    print(
                        f'Retry #{kwargs['timeout']} in {strftime("%H:%M:%S", gmtime(progressive_timeout[kwargs['timeout']] - j))}...'
                    )
                message_printed = True
                sleep(1)

        kwargs['timeout'] += 1

        func(**kwargs)


def main() -> None:
    # Get the pull request number
    pr_number: str | None = os.getenv('PR_NUMBER')
    commit_id: str | None = os.getenv('COMMIT_ID')
    personal_access_token: str | None = os.getenv('CLONELISTS_PAT')
    response: int = 0

    # Get uncommitted Git changes
    files = (
        subprocess.run(['git', 'diff', '--name-only'], stdout=subprocess.PIPE)
        .stdout.decode('utf-8')
        .split('\n')
    )
    files = [x for x in files if 'clonelists' in x]

    if not files:
        # Compare current commit and previous commit to get files that have changed
        if sys.platform.startswith('win'):
            files = (
                subprocess.run(
                    ['git', 'diff', 'HEAD~', 'HEAD', '--name-only'], stdout=subprocess.PIPE
                )
                .stdout.decode('utf-8')
                .split('\n')
            )
        else:
            files = (
                subprocess.run(
                    ['git', 'diff', 'HEAD^', 'HEAD', '--name-only'], stdout=subprocess.PIPE
                )
                .stdout.decode('utf-8')
                .split('\n')
            )

    files = [x for x in files if 'clonelists' in x]

    test_succeeded: bool = True

    for file in files:
        if file != 'hash.json':
            print(f'\n\nValidating {file}\n{'-----------'}{'-'*len(file)}\n')

            # Check for valid JSON
            error_messages: dict[int, dict[str, Any]] = {}
            clonelist: Any
            cloneliststr: str = ''

            try:
                with open(pathlib.Path(file), encoding='utf-8') as clone_list_file:
                    clonelist = json.load(clone_list_file)
                    # Read the clone list as a string. This is required to find the line
                    # number later for JSON schema validation errors.
                    clone_list_file.seek(0)
                    cloneliststr = clone_list_file.read()
            except json.decoder.JSONDecodeError as e:
                if e.lineno not in error_messages:
                    error_messages[e.lineno] = {}

                if 'comment' not in error_messages[e.lineno]:
                    error_messages[e.lineno]['comment'] = ''

                error_messages[e.lineno]['comment'] = (
                    '### :gear: Automated review comment\n\n'
                    f'Invalid JSON found on or before line ({e.lineno}). Fix the error to '
                    'continue.\n\nThere might be more invalid JSON in this file, but only '
                    'one line can be checked for at a time. To speed up error checking, try '
                    'an [online JSON validator](https://jsonlint.com/), or use an IDE that '
                    'can lint JSON like [Visual Studio code](https://code.visualstudio.com/) '
                    'to find errors before updating your PR.'
                )
                print(error_messages)

                add_comment(
                    timeout=0,
                    personal_access_token=personal_access_token,
                    pr_number=pr_number,
                    commit_id=commit_id,
                    filepath=file,
                    pr_comment=error_messages[e.lineno]['comment'],
                    line_number=e.lineno,
                )

                sys.exit(1)
            except Exception as e:
                print(f'Unexpected error reading JSON file: {e}')
                sys.exit(1)

            # Load the JSON schema
            with open(
                pathlib.Path('scripts/clone-list-schema.json'), encoding='utf-8'
            ) as schema_file:
                schema = json.load(schema_file)

            validator = jsonschema.Draft202012Validator(schema)

            # Get all schema validation errors
            errors = validator.iter_errors(clonelist)
            parent_comments: list[str] = []

            for error in errors:
                error_path = (
                    error.json_path.replace('.', '/')
                    .replace('$', '')
                    .replace('[', '/')
                    .replace(']', '')
                )
                comment: str = ''
                parent_comment: str = ''
                local_names_str: str = ''
                region_names_str: str = ''

                # If $ref is used in a schema, then we have to pull the appropriate parent
                # $comment instead of the $comment in the $ref.
                parent_comment_json_path = (
                    f'{re.sub("\\[[0-9]+\\]", "", error.json_path).replace('.', "..")}["$comment"]'
                )

                if '$..variants..titles..filters..' in parent_comment_json_path:
                    parent_comment_json_path = parent_comment_json_path.replace(
                        '..variants..titles', ''
                    )

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
                        error_messages[error_line][
                            'comment'
                        ] = f'{error_messages[error_line]["comment"]}\n\n{comment}'

                # Pull languages out of the appropriate $ref if localNames is being
                # queried
                if 'localNames' in error.json_path:
                    local_names_expr = jsonpath_ng.parse('$..languages..properties')
                    local_names = [match.value for match in local_names_expr.find(schema)]

                    if local_names:
                        local_names_str = '`, `'.join(
                            list([match.value for match in local_names_expr.find(schema)][0].keys())
                        )

                if local_names_str:
                    error_messages[error_line][
                        'comment'
                    ] = f'{error_messages[error_line]["comment"]}\n\nThe valid languages are as follows:\n\n`{local_names_str}`'

                # Pull regions out of the appropriate $ref if matchRegions is being
                # queried
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
                    error_messages[error_line][
                        'comment'
                    ] = f'{error_messages[error_line]["comment"]}\n\nThe valid regions are as follows:\n\n`{region_names_str}`'

                # Add the JSON schema error messages
                if 'errors' not in error_messages[error_line]:
                    error_messages[error_line]['errors'] = []

                error_messages[error_line]['errors'].append(error.message)

            if error_messages:
                print(error_messages)

                for line_number, error in error_messages.items():
                    validation_comment: str = (
                        '### :gear: Automated review comment\n\n'
                        'This line doesn\'t follow the '
                        '[clone list schema](https://raw.githubusercontent.com/unexpectedpanda/retool-clonelists-metadata/refs/heads/main/scripts/clone-list-schema.json).'
                        '\n\nHere\'s the comment from that part of the schema:\n\n'
                        f'> {error["comment"].replace('\n\n', '\n>\n>')}'
                        '\n\nHere\'s the validation error:\n\n'
                        f'> {error["errors"]}'
                    )

                    add_comment(
                        timeout=0,
                        personal_access_token=personal_access_token,
                        pr_number=pr_number,
                        commit_id=commit_id,
                        filepath=file,
                        pr_comment=validation_comment,
                        line_number=line_number,
                    )

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
                print(
                    f'Found the search term `{searchterm_name}` multiple times on the following lines:\n\n{"\n".join(str(f"* {x}") for x in searchterm_lines)}\n\nSearch terms for `titles` should only be associated with one `group`, and not be repeated within that `group`.'
                )

                duplicate_searchterm_comment: str = (
                    '### :gear: Automated review comment\n\n'
                    f'Found the search term `{searchterm_name}` multiple times on the following lines:'
                    f'\n\n{"\n".join(str(f"* {x}") for x in searchterm_lines)}\n\nSearch terms for `titles` '
                    'should only be associated with one `group`, and not be repeated within that `group`.'
                )

                print(
                    f'I should post a comment about the searchTerm {searchterm_name} on line {searchterm_lines[0]}'
                )

                # GitHub doesn't allow comments on unchanged lines in a PR. Cycle through until we find a changed line.
                response = 0

                for searchterm_line in searchterm_lines:
                    response = add_comment(
                        timeout=0,
                        personal_access_token=personal_access_token,
                        pr_number=pr_number,
                        commit_id=commit_id,
                        filepath=file,
                        pr_comment=duplicate_searchterm_comment,
                        line_number=searchterm_line,
                        dupe_check=True,
                    )

                    if response == 422:
                        continue
                    else:
                        break

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
                print(
                    f'Found the group `{group_name}` multiple times on the following lines:\n\n{"\n".join(str(f"* {x}") for x in group_lines)}\n\nThere should only be one instance of a `group` name in a `variants` array.'
                )

                duplicate_group_comment: str = (
                    '### :gear: Automated review comment\n\n'
                    f'Found the group `{group_name}` multiple times on the following lines:\n\n'
                    f'{"\n".join(str(f"* {x}") for x in group_lines)}\n\nThere should only be one instance '
                    'of a `group` name in a `variants` array.'
                )

                print(
                    f'I should post a comment about the group {group_name} on line {group_lines[0]}'
                )

                # GitHub doesn't allow comments on unchanged lines in a PR. Cycle through until we find a changed line.
                response = 0

                for group_line in group_lines:
                    response = add_comment(
                        timeout=0,
                        personal_access_token=personal_access_token,
                        pr_number=pr_number,
                        commit_id=commit_id,
                        filepath=file,
                        pr_comment=duplicate_group_comment,
                        line_number=group_line,
                        dupe_check=True,
                    )

                    if response == 422:
                        continue
                    else:
                        break

            if not error_messages and not group_dupes and not searchterm_dupes:
                print('No problems found.')
            else:
                test_succeeded = False

    if not test_succeeded:
        sys.exit(1)


if __name__ == '__main__':
    try:
        print('Starting validation...')
        main()
    except Exception:
        print('\n• Unexpected error:\n\n')
        traceback.print_exc()
        sys.exit(1)
