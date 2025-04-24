#!/usr/bin/env python3

import re
from datetime import datetime
from flask import Flask, jsonify
import aiohttp
import wikitextparser as wtp
from functools import wraps

app = Flask(__name__)
DEVICE_REGEX = re.compile(r'(iPhone|AppleTV|iPad|iPod)[0-9]+,[0-9]+')

def async_route(f):
    @wraps(f)
    async def wrapper(*args, **kwargs):
        return await f(*args, **kwargs)
    return wrapper

async def get_key_page(session: aiohttp.ClientSession, identifier: str, buildid: str) -> str:
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
        raise ValueError(f'No Firmware Keys page for device: {identifier}, buildid: {buildid}.')

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
    data = ' '.join([x for x in data.split(' ') if x != '']).replace('{{', '{| class="wikitable"').replace('}}', '|}')
    page = wtp.parse(data)
    page_data = {}
    
    for entry in page.tables[0].data()[0]:
        if ' = ' not in entry:
            continue
        key, item = entry.split(' = ')
        page_data[key] = item

    response = {
        'identifier': page_data.get('Device', identifier),
        'buildid': page_data.get('Build', ''),
        'codename': page_data.get('Codename', ''),
        'updateramdiskexists': 'UpdateRamdisk' in page_data,
        'restoreramdiskexists': 'RestoreRamdisk' in page_data,
        'keys': [],
    }

    for component in page_data:
        if component in ('Version', 'Build', 'Device', 'Model', 'Codename', 'Baseband', 'DownloadURL'):
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

        for key_type in ('IV', 'Key', 'KBAG'):
            key_name = component + key_type
            if key_name in page_data and all(x not in page_data[key_name] for x in ('Unknown', 'Not Encrypted')):
                image[key_type.lower()] = page_data[key_name]

        if (('iv' in image and 'key' in image) or 'kbag' in image):
            response['keys'].append(image)

    return response

@app.route('/firmware/<path:device_build>')
@async_route
async def firmware_keys(device_build):
    async with aiohttp.ClientSession() as session:
        parts = device_build.split('/')
        
        # Обрабатываем оба формата:
        # /firmware/iPhone10,6/20G81
        # /firmware/iPhone10,6/iPhone10,6/20G81
        if len(parts) == 2:
            device, buildid = parts
        elif len(parts) == 3:
            device, _, buildid = parts
        else:
            return jsonify({
                'error': 'Invalid URL format. Use /firmware/device/buildid or /firmware/device/device/buildid',
                'keys': []
            }), 400

        try:
            wikitext = await get_key_page(session, device, buildid)
            result = parse_page(wikitext, device)
            return jsonify(result)
        except Exception as e:
            return jsonify({
                'error': str(e),
                'identifier': device,
                'buildid': buildid,
                'codename': '',
                'updateramdiskexists': False,
                'restoreramdiskexists': False,
                'keys': []
            }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888)
