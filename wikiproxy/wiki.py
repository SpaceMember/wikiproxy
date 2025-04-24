#!/usr/bin/env python3

import re
from datetime import datetime

import aiohttp
import wikitextparser as wtp

DEVICE_REGEX = re.compile(r'(iPhone|AppleTV|iPad|iPod)[0-9]+,[0-9]+')


async def get_key_page(
    session: aiohttp.ClientSession, identifier: str, buildid: str
) -> str:
    params = {
        'action': 'query',
        'list': 'search',
        'srsearch': f'intitle:{identifier} {buildid}',
        'srlimit': '1',
        'format': 'json',
        'srnamespace': '2304',
    }
    async with session.get('https://theapplewiki.com/api.php', params=params) as resp:
        if resp.status != 200:
            raise ValueError(f'API request failed with status {resp.status}')
        search = await resp.json()

    if search['query']['searchinfo']['totalhits'] == 0:
        raise ValueError(
            f'No Firmware Keys page for device: {identifier}, buildid: {buildid}.'
        )

    params = {
        'action': 'parse',
        'prop': 'wikitext',
        'page': search['query']['search'][0]['title'],
        'format': 'json',
        'formatversion': 2,
    }
    async with session.get('https://theapplewiki.com/api.php', params=params) as resp:
        if resp.status != 200:
            raise ValueError(f'API request failed with status {resp.status}')
        data = await resp.json()

    return data['parse']['wikitext']


def parse_page(data: str, identifier: str, boardconfig: str = None) -> dict:
    # Have to coerce wikitextparser into recognizing it as a table for easy parsing
    data = (
        ' '.join([x for x in data.split(' ') if x != ''])
        .replace('{{', '{| class="wikitable"')
        .replace('}}', '|}')
    )

    page = wtp.parse(data)
    page_data = {}
    for entry in page.tables[0].data()[0]:
        if ' = ' not in entry:
            continue
        key, item = entry.split(' = ')
        page_data[key] = item

    if boardconfig is not None:
        if ('Model' not in page_data.keys()) and ('Model2' not in page_data.keys()):
            return page_data

        if boardconfig.lower() not in [x.lower() for x in page_data.values()]:
            raise ValueError(
                f'Boardconfig: {boardconfig} for device: {identifier} is not valid!'
            )

        if 'Model2' in page_data and page_data['Model2'].lower() == boardconfig.lower():
            keys_list = list(page_data.keys())
            for key in keys_list:
                if '2' in key:
                    page_data[key.replace('2', '')] = page_data[key]

        for key in list(page_data.keys()):
            if '2' in key:
                del page_data[key]

    response = {
        'identifier': page_data.get('Device', identifier),
        'buildid': page_data.get('Build', ''),
        'codename': page_data.get('Codename', ''),
        'updateramdiskexists': 'UpdateRamdisk' in page_data,
        'restoreramdiskexists': 'RestoreRamdisk' in page_data,
        'keys': [],
    }

    for component in page_data:
        if component in (
            'Version',
            'Build',
            'Device',
            'Model',
            'Codename',
            'Baseband',
            'DownloadURL',
        ):
            continue

        if any(component.endswith(x) for x in ('Key', 'IV', 'KBAG')):
            continue

        image = {
            'image': component,
            'filename': page_data[component],
            'date': datetime.now().isoformat(),
        }

        if any(component == x for x in ('RootFS', 'RestoreRamdisk', 'UpdateRamdisk')):
            image['filename'] += '.dmg'

        # Add IV, Key and KBAG if available
        for key_type in ('IV', 'Key', 'KBAG'):
            key_name = component + key_type
            if key_name in page_data and all(
                x not in page_data[key_name] for x in ('Unknown', 'Not Encrypted')
            ):
                image[key_type.lower()] = page_data[key_name]

        # Only add if we have at least IV+Key or KBAG
        if (('iv' in image and 'key' in image) or 'kbag' in image):
            response['keys'].append(image)

    return response


async def get_keys(
    session: aiohttp.ClientSession, 
    device: str, 
    buildid: str, 
    boardconfig: str = None
) -> dict:
    try:
        wikitext = await get_key_page(session, device, buildid)
        return parse_page(wikitext, device, boardconfig)
    except Exception as e:
        return {
            'error': str(e),
            'identifier': device,
            'buildid': buildid,
            'codename': '',
            'updateramdiskexists': False,
            'restoreramdiskexists': False,
            'keys': []
        }
