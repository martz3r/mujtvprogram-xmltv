import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import os, requests, unicodedata, logging
import re

try:
    import roman
except ImportError:
    logging.error('Error importing "roman" package, extract from programme title disabled')
    roman = None


class Channel: 
    id:str
    display_names:list[tuple[str, str|None]]
    category:str|None
    icon_url:str|None

    def __init__(self, channel_id:str, display_names:list[tuple[str,str|None]], category:str|None=None, icon_url:str|None=None) -> None:
        self.id = channel_id
        self.display_names = display_names
        self.category = category
        self.icon_url = icon_url

class Programme:
    channel_id:str
    title:str
    description:str|None

    start:datetime
    stop:datetime|None

    categories:list[str]
    ratings:list[dict]
    credits:list[dict]
    images:list[dict]
    
    video_info:dict
    audio_info:dict

    is_premiere:bool
    has_subtitles:bool
    release_date:datetime|None

    season_num:int|None
    episode_num:int|None

    def __init__(self, channel_id:str, title:str, desc:str|None, categories:list=[]) -> None:
        self.channel_id = channel_id
        self.title = title
        self.description = desc
        self.categories = categories
        self.start = datetime.now()
        self.stop = None
        self.ratings = []
        self.credits = []
        self.images = []
        self.video_info = {}
        self.audio_info = {}
        self.is_premiere = False
        self.has_subtitles = False
        self.release_date = None
        self.season_num = None
        self.episode_num = None

    def set_time(self, start:datetime, stop:datetime|None):
        self.start = start
        self.stop = stop

    def set_serie(self, season:int|None, episode:int|None):
        self.season_num = season
        self.episode_num = episode

    def add_rating(self, system:str, value:str, url:str|None):
        self.ratings.append({'system':system, 'value':value, 'url':url})

    def add_credit(self, type:str, value:str, role:str|None):
        self.credits.append({'type':type, 'value':value, 'role': role})

    def add_category(self, category:str):
        self.categories.append(category)


# -----  Time Methods  -----
def to_xmltv_time(dt: datetime,) -> str:
    """Convert a timezone-aware datetime to XMLTV's 'YYYYMMDDHHMMSS +HHMM' format."""
    if dt.tzinfo is None:
        raise ValueError(f"to_xmltv_time got a naive datetime: {dt!r}")
    return dt.strftime("%Y%m%d%H%M%S %z")

def parse_xmltv_str(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y%m%d%H%M%S %z")

def parse_epoch(ts: str) -> datetime:
    """API's *DateTimeInSec fields are real UTC unix timestamps."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)
 
def parse_local_string(dt_str: str) -> datetime:
    """Fallback: 'DD.MM.YYYY HH:MM' is a Prague *local* wall-clock time."""
    naive = datetime.strptime(dt_str, '%d.%m.%Y %H:%M')
    return naive.replace(tzinfo=LOCAL_TZ)

# -----  Parsing  -----
def _id_from_name(name: str) -> str:
    name = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    name = name.replace(' ', '_')
    return name

def _parse_id_map(data) -> dict[str,dict]:
    id_map = {}
    root = ET.fromstring(data)
    mappings = root.findall('.//map')
    logging.debug(f'[id_map] {len(mappings)} channel(s) id')
    for _map in mappings:
        ch_name = _map.text
        cid = _map.get('id1')
        iptv_id = _map.get('id2')
        if cid is not None and iptv_id is not None:
            id_map[str(cid)] = {'id': iptv_id, 'name':ch_name}

    for _id, val in id_map.items():
        unique_id.add(_id)
        unique_id.add(val['id'])

    return id_map
        
def _get_id(cid:str, name:str, id_map:dict={}):
    _map = id_map.get(str(cid), {})
    _id = _map.get('id', None)
    if not _id or str(_id).strip() == '':
        _id = _id_from_name(name)
        id_map[str(cid)] = {'id': _id, 'name': name}
        logging.warning(f'Fallback ID "{_id}" for "{name}"')
    return (_id, id_map)

def _extract_season_episode(text: str) -> tuple[int | None, int | None]:
    if not roman:
        return None, None

    text = text.strip()
    season = None
    episode = None

    # 1. Try to find episode number in parentheses
    ep_match = re.search(r'\((\d+)\)', text)
    if ep_match:
        episode = int(ep_match.group(1))
        text = re.sub(r'\(\d+\)', '', text).strip()

    # 2. Try to find a Roman numeral at the end (after stripping episode)
    roman_match = re.search(r'\b([IVXLCDM]+)$', text)
    if roman_match:
        try:
            season = roman.fromRoman(roman_match.group(1))
        except roman.InvalidRomanNumeralError:
            pass  # not a valid Roman numeral, ignore

    return season, episode

# -----  XMLTV Creation Methods  -----
def _mk_xmltv_channel(channel:Channel) -> ET.Element:
    elem = ET.Element("channel", id=str(channel.id))  # e.g. "bbc1.uk"
 
    for name, lang in channel.display_names:
        if lang:
            dn = ET.SubElement(elem, "display-name", lang=lang)
        else:
            dn = ET.SubElement(elem, "display-name")
        dn.text = name
 
    if channel.icon_url:
        ET.SubElement(elem, "icon", src=channel.icon_url)
 
    return elem

def _mk_xmltv_programme(programme:Programme) -> ET.Element:
    if programme.stop is not None:
        root_elem = ET.Element("programme",channel=str(programme.channel_id), start=to_xmltv_time(programme.start), stop=to_xmltv_time(programme.stop))
    else:
        root_elem = ET.Element("programme",channel=str(programme.channel_id), start=to_xmltv_time(programme.start))
 
    title_elem = ET.SubElement(root_elem, "title")
    title_elem.text = programme.title
 
    if programme.description:
        elem = ET.SubElement(root_elem, "desc")
        elem.text = programme.description

    if programme.is_premiere:
        elem = ET.SubElement(root_elem, 'premiere')
        elem.text = 'Premiere'

    if programme.season_num or programme.episode_num:
        s = str(programme.season_num) if programme.season_num is not None else "0"
        e = str(programme.episode_num) if programme.episode_num is not None else "0"
        ET.SubElement(root_elem, 'episode-num', system='xmltv_ns').text = f"{s}.{e}"

    if programme.release_date:
        ET.SubElement(root_elem, 'date').text = str(programme.release_date.year) # Just year no need for fill xmltv convert  "to_xmltv_time(programme.release_date, True)"

    if programme.has_subtitles:
        ET.SubElement(root_elem, 'subtitles')
 
    if programme.categories:
        for category in programme.categories:
            elem = ET.SubElement(root_elem, "category")
            elem.text = category

    if programme.credits:
        credits = ET.SubElement(root_elem, "credits")
        for credit in programme.credits:
            elem = ET.SubElement(credits, credit['type']) # role can be added as attrib
            elem.text = credit['value']

    if programme.ratings:
        for rating in programme.ratings:
            system_str = f"{rating['system']}|{rating['url']}" if rating.get('url') else rating['system']
            rating_elem = ET.SubElement(root_elem, "star-rating", attrib={'system': system_str})
            rating_elem.text = rating['value']
            if rating.get('url', None):
                review_elem = ET.SubElement(root_elem, 'review', attrib={'type': 'text', 'source': rating['system'], 'url': rating['url']})
                review_elem.text = f"{rating['value']} on {rating['system']}"
                #url_elem = ET.SubElement(root_elem, 'url', attrib={'system': rating['system']})
                #url_elem.text = rating['url']

    if programme.video_info:
        video_elem = ET.SubElement(root_elem, "video")
        for k,v in programme.video_info.items():
            if v is not None:
                elem = ET.SubElement(video_elem, k)
                elem.text = v

    if programme.audio_info:
        audio_elem = ET.SubElement(root_elem, "audio")
        for k,v in programme.audio_info.items():
            if v is not None:
                elem = ET.SubElement(audio_elem, k)
                elem.text = v

    if programme.images:
        for img in programme.images:
            img_elem = ET.SubElement(root_elem, 'image', attrib={'type': img['type']})
            img_elem.text = img['url']
 
    return root_elem

# ---- Channel & Programme class Methods -----
def _fix_overlaps(programmes:list[Programme]):
    by_channel = defaultdict(list[tuple[float|int, Programme]])
    seen_first = {}  # (channel, start, title) -> True, for de-duplication
    fixed_elems:list[tuple[float|int, Programme]] = []

    for prog in programmes:
        
        by_channel[prog.channel_id].append((prog.start.timestamp(), prog)) 

    for ch, items in by_channel.items():
        items.sort(key=lambda x: x[0])
        for i, (start, prog) in enumerate(items):
            title = prog.title
            key = (ch, prog.start.timestamp(), title)
            if key in seen_first:
                continue  # drop duplicate
            seen_first[key] = True
 
            if i + 1 < len(items):
                next_start = items[i + 1][0]
                stop = prog.stop.timestamp() if prog.stop else None
                if stop is None or stop > next_start:
                    prog.stop = datetime.fromtimestamp(next_start, LOCAL_TZ)
 
            fixed_elems.append((start, prog))

    fixed_elems.sort(key=lambda p: p[0])
    return [x[1] for x in fixed_elems]


def _fill_gaps(programmes:list[Programme], placeholder:Programme, min_gap_minutes:float=1, ):
    by_channel = defaultdict(list)
    all_elems:list[tuple[int, Programme]] = []

    for prog in programmes:
        by_channel[prog.channel_id].append((prog.start, prog.stop, prog))

    for ch, items in by_channel.items():
        items.sort(key=lambda p: p[0])
        prev_stop = None
        for start, stop, prog in items:
            if prev_stop is not None:
                gap_min = (start - prev_stop).total_seconds() / 60
                if gap_min >= min_gap_minutes:
                    filler = Programme(ch, placeholder.title, placeholder.description, placeholder.categories)
                    filler.set_time(prev_stop, start)
                    all_elems.append((prev_stop, filler))
            all_elems.append((start, prog))
            if stop is not None:
                prev_stop = stop
 
    all_elems.sort(key=lambda p: p[0])
    return [x[1] for x in all_elems]  

def _get_programmes(day:int=0, id_map:dict[str,dict] = {}):
    date = datetime.now().date() + timedelta(day)
    logging.info(f'Pulling day {date.strftime("%d.%m.%Y")}')

    programmes = []
    for cid in cid_array:
        url = f'{programme_url}?channel_cid={cid}&day={day}'
        try:
            response = requests.get(url)
            response.raise_for_status()
        except requests.RequestException as e:
            logging.error(f'[ChannelURL] RequestException: {e}')
            continue
        except Exception as e:
            logging.error(f'[ChannelURL] Exception: {e}')
            continue

        root = ET.fromstring(response.content)
        programs = root.findall('.//programme')

        _map = id_map[str(cid)]
        _name = _map.get('name', None)
        logging.debug(f'\t{len(programs)} programms for "{_name if _name else _map.get("id")}"')
        for programme in programs:
            if ts := programme.findtext('startDateTimeInSec'):
                start = parse_epoch(ts)
            elif dt := programme.findtext('startDate'):
                start = parse_local_string(dt)
            else:
                start = None
                logging.warning(f"No start time for channel={cid} programme_id={programme.findtext('id')}")
        
            if ts := programme.findtext('endDateTimeInSec'):
                stop = parse_epoch(ts)
            elif dt := programme.findtext('endDate'):
                stop = parse_local_string(dt)
            else:
                stop = None
        
            title = programme.findtext('name')
            desc = programme.findtext('longDescription') or programme.findtext('shortDescription') or None

            _id = _map.get('id')

            if _id and start and title:
                
                prog = Programme(_id, title, desc, [])
                prog.set_time(start, stop)

                categories = [category for prog_type in programme.findall('.//programme-type') if (category := prog_type.findtext('name'))]
                for category in categories:
                    prog.add_category(category)

                prog.is_premiere = (int(programme.findtext('premier', 0)) == 1)
                prog.has_subtitles = (int(programme.findtext('subtitles', 0)) == 1)
                if year := programme.findtext('year', None):
                    prog.release_date = datetime.strptime(year, '%Y')

                # Get crew
                actors = act_text.split(',') if (act_text := programme.findtext('actors')) else []
                directors = dir_text.split(',') if (dir_text := programme.findtext('directors')) else []

                for actor in actors:
                    prog.add_credit('actor', actor.strip(), None)
                for director in directors:
                    prog.add_credit('director', director.strip(), None)

                # Get Rating
                if fdbRating := programme.findtext('fdbRating'): 
                    if 0 < int(fdbRating) <= 100: # Skip if lower or 0 or just bigger than 100
                        fdbRating = f'{fdbRating}/100' # change to format "x/100", range should be 0-100
                        prog.add_rating('FDb', fdbRating, programme.findtext('fdbUrl'))

                # Video & Audio Info
                prog.video_info['aspect'] = '16:9' if int(programme.findtext('widescreen', 0)) == 1 else None
                prog.video_info['quality'] = 'HDTV' if int(programme.findtext('hd', 0)) == 1 else None
                prog.video_info['color'] = 'no' if int(programme.findtext('blackWhite', 0)) == 1 else 'yes'
                prog.audio_info['stereo'] = 'stereo' if int(programme.findtext('stereo', 0)) == 1 else None

                # If series element exist, it may be series & contain season & episode number in title
                if programme.findtext('series', None): 
                    season_num, episode_num = _extract_season_episode(title)
                    prog.set_serie(season_num, episode_num)

                # Add image 
                for pic in programme.findall('.//picture'):
                    pic_name = pic.findtext('name')
                    pic_url = pic.findtext('url')
                    prog.images.append({'name': pic_name, 'url': pic_url, 'type': 'still'}) # Use still, there is no info for it from source

                programmes.append(prog)

    return programmes

def _pull_channels(cid_list:list[int], id_map:dict):
    channels:list[Channel] = []
    url = f'{channel_url}?channel_cid_arr={",".join([str(x) for x in cid_list])}&localization=1'
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f'[ChannelURL] RequestException: {e}')
        exit(1)
    except Exception as e:
        logging.error(f'[ChannelURL] Exception: {e}')
        exit(1)

    root = ET.fromstring(response.content)
    xml_channels = root.findall('.//channel')
    logging.info(f'Got {len(xml_channels)} channels')
    for ch in xml_channels:
        cid = ch.findtext("cid")
        name = ch.findtext("name")
        lang = ch.findtext("lang")
        icon_url = ch.findtext("logo-image/url") or None

        if cid is not None and name is not None:
            _id, id_map = _get_id(cid, name, id_map)
            ch_elem = Channel(_id, [(name, lang)], None, icon_url)
            channels.append(ch_elem)
    return channels

# ----- XML ----

def _create_root(date:datetime, generator_name:str = "mujtvprogram-fetch") -> ET.Element:
    return ET.Element("tv", attrib={"generator-info-name": generator_name, "date": to_xmltv_time(date)})

def _save_xml_tree(element:ET.Element, file):
    tree = ET.ElementTree(element)
    ET.indent(tree, space="  ")
    tree.write(file, encoding="UTF-8", xml_declaration=True)
    try:
        logging.info(f'XML Tree saved as "{file.name}"') # If is IO object with name & write attribs
    except AttributeError:
        logging.info(f'XML Tree saved as "{file}"')

# ====================================    Methods    ====================================

def merge_xmltv(channel, *programme): ... 
def pull_seperate(channel_path:str, programme_path:str): ...

def pull_guide(file, days:list[int], filler_programme:Programme|None = None, id_map:dict={}):
    """Pulls guide for days relative to today"""
    channels = _pull_channels(cid_array, id_map)
    programmes:list[Programme] = []
    for d in days:
        progs = _get_programmes(d, id_map)
        programmes.extend(progs)

    programmes = _fix_overlaps(programmes)
    if filler_programme:
        programmes = _fill_gaps(programmes, filler_programme)
        
    logging.info(f'Total {len(channels)} Channels & {len(programmes)} Programme entries')
    logging.info('Converting objects to xml elements...')

    tv_root = _create_root(datetime.now(LOCAL_TZ))
    for ch in channels: tv_root.append(_mk_xmltv_channel(ch))
    for prog in programmes: tv_root.append(_mk_xmltv_programme(prog))
    _save_xml_tree(tv_root, file)

### ====================================    Main Run    ====================================

cwd = os.path.split(__file__)[0]
LOCAL_TZ = ZoneInfo("Europe/Prague")
filler_programme:Programme|None = Programme('', 'No Information', 'No data from service', ['Off Air'])
now = datetime.now(LOCAL_TZ)

# MujTVProgram URLS
channel_url = 'https://services.mujtvprogram.cz/tvprogram2services/services/tvchannellist_mobile.php'
programme_url = 'https://services.mujtvprogram.cz/tvprogram2services/services/tvprogrammelist_mobile.php'
cid_array:list[int] = [1,2,3,4,7,9,10,12,14,15,21,24,30,63,64,65,78,89,92,112,121,
                       125,207,226,271,272,333,369,370,394,397,459,474,558,559,560,
                       606,608,818,897,921,923,1049]

# ID Mapping
id_mapfile = os.path.join(cwd, 'id_map.xml')
id_map = {}
unique_id = set([])

if id_mapfile and os.path.exists(id_mapfile):
    logging.info('Found Map File')
    with open(id_mapfile, 'rb') as f:
        id_map = _parse_id_map(f.read())

