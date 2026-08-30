#!/usr/bin/env python3
import base64, html, io, json, os, re, subprocess, sys, traceback, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from openai import OpenAI

ROOT=Path(__file__).resolve().parents[1]; QUEUE=ROOT/'queue'; MEDIA=ROOT/'media'
DEFAULT_FEEDS=(
    'https://news.google.com/rss/search?q=WNBA+breaking+news+trade+signing+injury&hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=NBA+breaking+news+trade+signing+contract&hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=NFL+breaking+news+trade+signing+contract&hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=MLB+breaking+news+trade+signing+contract&hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=NHL+breaking+news+trade+signing+contract&hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=NCAA+breaking+news+transfer+deal+record&hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=amateur+sports+viral+record+remarkable&hl=en-US&gl=US&ceid=US:en',
)
FEED_URLS=tuple(x.strip() for x in os.environ.get('SPORTS_RSS_URLS','').split(',') if x.strip()) or DEFAULT_FEEDS
MAX_CANDIDATES=int(os.environ.get('MAX_NEW_ITEMS','30')); MAX_AGE_HOURS=max(24,int(os.environ.get('MAX_SOURCE_AGE_HOURS','24')))
FONT_BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'; FONT_REG='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
INK=(8,12,20); PAPER=(246,247,242); ACID=(203,255,0); BLUE=(0,82,255); ORANGE=(255,74,30)
client=OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

def clean(v):
    v=html.unescape(v or ''); v=re.sub(r'<[^>]+>',' ',v); return re.sub(r'\s+',' ',v).strip()

def tag_text(item,name):
    for child in item:
        if child.tag.rsplit('}',1)[-1].lower()==name.lower(): return clean(child.text)
    return ''

def raw_tag_text(item,name):
    for child in item:
        if child.tag.rsplit('}',1)[-1].lower()==name.lower():
            return ''.join(child.itertext()) or child.text or ''
    return ''

def usable_image_url(url,base=''):
    if not url:return ''
    url=html.unescape(url.strip()); url=urllib.parse.urljoin(base,url)
    return url if url.startswith(('https://','http://')) else ''

def feed_image_url(item,link):
    for child in item:
        local=child.tag.rsplit('}',1)[-1].lower(); url=child.attrib.get('url') or child.attrib.get('href') or ''; kind=(child.attrib.get('type') or '').lower()
        looks_like_image=bool(re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)',url,re.I))
        if url and (local=='thumbnail' or kind.startswith('image/') or looks_like_image):
            found=usable_image_url(url,link)
            if found:return found
    raw=' '.join((raw_tag_text(item,'description'),raw_tag_text(item,'encoded')))
    match=re.search(r'<img[^>]+src=["\']([^"\']+)',raw,re.I)
    return usable_image_url(match.group(1),link) if match else ''

def discover_source_image(source):
    candidates=[]
    if source.get('image_url'):candidates.append(source['image_url'])
    req=urllib.request.Request(source['link'],headers={'User-Agent':'Mozilla/5.0 CrashOutSports/1.0'})
    try:
        with urllib.request.urlopen(req,timeout=30) as response:page=response.read(2_000_000).decode('utf-8','ignore')
    except Exception:page=''
    patterns=(
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image(?::src)?)["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image(?::src)?)["\']',
        r'<img[^>]+src=["\']([^"\']+)',
    )
    for pattern in patterns:
        match=re.search(pattern,page,re.I)
        if match:
            found=usable_image_url(match.group(1),source['link'])
            if found and found not in candidates:candidates.append(found)
    for candidate in candidates:
        try:return candidate,image_data_url(candidate)
        except Exception as error:print('Reference candidate failed:',candidate,error)
    raise RuntimeError('Selected story has no usable event-specific visual reference; Crash Out Sports will not invent a generic scene')

def image_data_url(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 CrashOutSports/1.0','Accept':'image/*'})
    with urllib.request.urlopen(req,timeout=45) as response:raw=response.read(12_000_001)
    if len(raw)>12_000_000:raise RuntimeError('Source reference image exceeds 12 MB')
    image=Image.open(io.BytesIO(raw)).convert('RGB'); image.thumbnail((1600,1600),Image.Resampling.LANCZOS)
    out=io.BytesIO(); image.save(out,'JPEG',quality=92,optimize=True)
    return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode('ascii')

def pub_date(v):
    if not v:return None
    try: dt=parsedate_to_datetime(v)
    except Exception:
        try: dt=datetime.fromisoformat(v.replace('Z','+00:00'))
        except Exception:return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

def parse_feed():
    now=datetime.now(timezone.utc); cutoff=now.timestamp()-MAX_AGE_HOURS*3600; items=[]
    for feed_url in FEED_URLS:
        try:
            req=urllib.request.Request(feed_url,headers={'User-Agent':'CrashOutSports-SourceMonitor/1.0'})
            with urllib.request.urlopen(req,timeout=30) as r: raw=r.read()
            root=ET.fromstring(raw)
        except Exception as error:
            print('Feed failed:',feed_url,error); continue
        for item in root.iter():
            if item.tag.rsplit('}',1)[-1].lower()!='item': continue
            title=tag_text(item,'title'); desc=tag_text(item,'description') or tag_text(item,'encoded'); link=tag_text(item,'link')
            guid=tag_text(item,'guid') or link or title; dt=pub_date(tag_text(item,'pubDate') or tag_text(item,'published') or tag_text(item,'date'))
            if title and dt and cutoff<=dt.timestamp()<=now.timestamp():
                link=link or feed_url
                items.append({'id':guid,'title':title,'description':desc[:3500],'link':link,'published':dt.isoformat(),'image_url':feed_image_url(item,link)})
    seen=set(); out=[]
    for x in sorted(items,key=lambda a:a['published'],reverse=True):
        k=re.sub(r'[^a-z0-9]+',' ',x['title'].lower()).strip()
        if k not in seen: seen.add(k); out.append(x)
    if not out: raise RuntimeError('All sports feeds failed or contained no current stories')
    return out[:MAX_CANDIDATES]

def existing():
    seen=set()
    for p in QUEUE.glob('*.json'):
        try:
            d=json.loads(p.read_text())
            for k in ('source_guid','source_url','story_fingerprint'):
                if d.get(k):seen.add(str(d[k]))
            seen.update(str(x) for x in d.get('source_urls',[]))
        except Exception: pass
    return seen

def choose(cands):
    text='\n\n'.join(f"[{i}] TITLE: {c['title']}\nPUBLISHED: {c['published']}\nSOURCE: {c['link']}\nDETAILS: {c['description']}" for i,c in enumerate(cands))
    prompt=f'''You are the senior editor for Crash Out Sports, a fast, entertaining, independent sports newsroom. Pick ONE genuinely fresh and publishable story from this feed. Core coverage is WNBA, NBA, NFL, MLB, NHL and NCAA. Give the WNBA equal editorial weight. Also allow major soccer, combat sports, golf, tennis, motorsports, international, amateur, high-school, youth and unusual sports stories when verified and genuinely exceptional.

Prioritize confirmed trades, free-agent signings, contract extensions, releases, waivers, injuries, suspensions, coaching/front-office moves, draft developments, scores, records, ownership and media-rights deals, athlete business deals, and remarkable verified moments. Reject filler, recycled highlights, engagement bait, stale stories, unsupported rumors, gambling picks and stories involving minors where identification is unnecessary. Use web search and confirm the core claim with at least TWO independent credible sources. Prefer official league/team/school/player/agent statements and established reporting. A feed post may be a lead, but it is not sufficient confirmation by itself.

For trades and contracts, verify teams, players, years, total value, guaranteed money, picks, protections, conditions, physicals and league-approval status before stating details. For injuries, do not diagnose beyond official reporting. Clearly label a report, negotiation, rumor, allegation or pending transaction; never write it as confirmed.

Return ONLY JSON: {{"index":number,"league":string,"story_type":string,"confirmation_status":"confirmed"|"reported"|"pending"|"rumor","headline":string,"story":string,"caption":string,"visual_scene":string,"source_label":string,"verification_sources":[{{"name":string,"url":string}}],"featured_person":string,"instagram_handle":string,"instagram_profile_url":string,"carousel_pages":2_or_3,"extra_context":string}}.
Headline max 95 characters and factual. Story must explain what happened and why it matters. verification_sources must contain at least two distinct HTTPS URLs that directly support the core claim. visual_scene must describe the specific event shown by the selected source image, not an invented or abstract scene. Verify any displayed Instagram handle through web search; never infer it from a name. Use two carousel pages by default and three only for a genuinely complex deal, trade package or multi-part development.
CANDIDATES:\n{text}'''
    r=client.responses.create(model='gpt-5.6-luna',tools=[{'type':'web_search'}],input=prompt)
    m=re.search(r'\{.*\}',r.output_text.strip(),re.S)
    if not m: raise RuntimeError('AI editor did not return JSON')
    result=json.loads(m.group(0)); idx=int(result['index'])
    if idx<0 or idx>=len(cands): raise RuntimeError('AI selected invalid candidate')
    result['source_item']=cands[idx]; return result

def generate_art(scene,headline,reference_data_url):
    prompt=f'''Create ONE original editorial illustration for Crash Out Sports, a premium independent sports-news brand.
ACTUAL EVENT TO DEPICT: {scene}
REFERENCE PHOTO ROLE: The supplied source image is the factual visual reference. Preserve the recognizable people, identity, number of people, clothing, setting, pose, important props, event details and overall camera direction that make this specific moment newsworthy. Do not replace it with a generic imagined scene.

STYLE: kinetic American sports-poster editorial art with bold hand-inked linework, screen-print halftone texture, dramatic stadium lighting, strong motion and premium newsstand composition. Use a black, electric blue, acid green, off-white and restrained orange palette. The actual event must be obvious. If a public figure is central, make an editorial illustrated depiction rather than a photorealistic copy.

ABSOLUTE RULES: materially redraw the reference as original editorial art rather than copying pixels or tracing line-for-line; no text; no captions; no logos; no watermark; no letters; no fake newspaper masthead; no generic abstract shapes; no vector clip-art look. Do not add people, objects or actions unsupported by the reference and reporting. Keep every visible head, face, hair, hand, award and essential subject completely inside the canvas with generous crop-safe margin. Leave clean breathing room near the top for typography added later.
Headline context: {headline}'''
    r=client.responses.create(model='gpt-5.6-luna',input=[{'role':'user','content':[{'type':'input_text','text':prompt},{'type':'input_image','image_url':reference_data_url,'detail':'high'}]}],tools=[{'type':'image_generation','model':'gpt-image-2','action':'generate','size':'1024x1536','quality':'high','output_format':'jpeg','output_compression':92,'background':'opaque'}],tool_choice={'type':'image_generation'})
    vals=[o.result for o in r.output if getattr(o,'type','')=='image_generation_call']
    if not vals: raise RuntimeError('GPT-Image-2 returned no image')
    return base64.b64decode(vals[0])

def fnt(n,b=True): return ImageFont.truetype(FONT_BOLD if b else FONT_REG,n)
def wrap(d,text,font,width):
    lines=[]; cur=''
    for w in text.split():
        t=(cur+' '+w).strip()
        if d.textbbox((0,0),t,font=font)[2]<=width:cur=t
        else:
            if cur:lines.append(cur)
            cur=w
    if cur:lines.append(cur)
    return lines

def fit_head(d,text,width,max_lines):
    for n in range(74,39,-3):
        ff=fnt(n); ls=wrap(d,text.upper(),ff,width)
        if len(ls)<=max_lines:return ff,ls
    ff=fnt(40); return ff,wrap(d,text.upper(),ff,width)[:max_lines]

def person_tag(draw,x,y,label):
    if not label:return
    ff=fnt(25); width=int(draw.textlength(label,font=ff))+34
    draw.rounded_rectangle((x,y,x+width,y+48),radius=8,fill=INK,outline=ACID,width=3)
    draw.text((x+17,y+9),label,font=ff,fill=PAPER)

def assets(story_id,headline,story,art_bytes,source_label,person_label='',carousel_pages=2,extra_context=''):
    MEDIA.mkdir(parents=True,exist_ok=True); art_path=MEDIA/f'{story_id}-art.jpg'; art_path.write_bytes(art_bytes); art=Image.open(art_path).convert('RGB')
    W,H=1080,1350; s1=Image.new('RGB',(W,H),INK); d=ImageDraw.Draw(s1); hero=ImageOps.fit(art,(1000,800),method=Image.Resampling.LANCZOS,centering=(.5,.42)); s1.paste(hero,(40,40))
    d.rectangle((40,40,430,100),fill=ACID); d.text((58,52),'CRASH OUT SPORTS',font=fnt(27),fill=INK); person_tag(d,58,800,person_label); d.rectangle((40,875,1040,1310),fill=BLUE)
    hf,ls=fit_head(d,headline,900,4); y=915
    for line in ls:d.text((72,y),line,font=hf,fill=PAPER); y+=hf.size+9
    d.text((72,1260),f'{source_label.upper()}  •  CRASH OUT SPORTS',font=fnt(22),fill=ACID); p1=MEDIA/f'{story_id}-slide-1.jpg'; s1.save(p1,quality=94,optimize=True)
    s2=Image.new('RGB',(W,H),PAPER); d=ImageDraw.Draw(s2); d.rectangle((0,0,W,18),fill=ACID); d.text((58,55),'CRASH OUT SPORTS',font=fnt(38),fill=INK); d.text((58,135),'WHAT HAPPENED',font=fnt(38),fill=ORANGE); d.rectangle((58,195,1022,201),fill=INK)
    ls=wrap(d,story.replace('\n',' '),fnt(38,False),900); y=245
    for line in ls[:17]:d.text((70,y),line,font=fnt(38,False),fill=INK); y+=51
    d.rectangle((58,1195,1022,1201),fill=ACID); d.text((58,1235),f'SOURCE: {source_label}',font=fnt(27),fill=INK); d.text((58,1285),'DEALS  •  SCORES  •  CULTURE',font=fnt(25),fill=ORANGE); p2=MEDIA/f'{story_id}-slide-2.jpg'; s2.save(p2,quality=94,optimize=True); slides=[p1,p2]
    if carousel_pages==3 and extra_context:
        s3=Image.new('RGB',(W,H),PAPER); d=ImageDraw.Draw(s3); d.rectangle((0,0,W,18),fill=ACID); d.text((58,55),'CRASH OUT SPORTS',font=fnt(38),fill=INK); d.text((58,135),'MORE CONTEXT',font=fnt(38),fill=ORANGE); d.rectangle((58,195,1022,201),fill=INK)
        ls=wrap(d,extra_context.replace('\n',' '),fnt(38,False),900); y=245
        for line in ls[:17]:d.text((70,y),line,font=fnt(38,False),fill=INK); y+=51
        d.rectangle((58,1195,1022,1201),fill=ACID); d.text((58,1235),f'SOURCE: {source_label}',font=fnt(27),fill=INK); d.text((58,1285),'SLIDE 3 OF 3  •  CRASH OUT SPORTS',font=fnt(22),fill=ORANGE); p3=MEDIA/f'{story_id}-slide-3.jpg'; s3.save(p3,quality=94,optimize=True); slides.append(p3)
    SW,SH=1080,1920; st=Image.new('RGB',(SW,SH),INK); sd=ImageDraw.Draw(st); sa=ImageOps.fit(art,(980,1040),method=Image.Resampling.LANCZOS,centering=(.5,.4)); st.paste(sa,(50,50)); sd.rectangle((50,50,430,102),fill=ACID); sd.text((68,60),'CRASH OUT SPORTS',font=fnt(25),fill=INK); person_tag(sd,62,1050,person_label); sd.rectangle((50,1135,1030,1860),fill=BLUE); sf,sl=fit_head(sd,headline,880,5); y=1185
    for line in sl:sd.text((78,y),line,font=sf,fill=PAPER); y+=sf.size+8
    sd.text((78,1800),'CRASH OUT SPORTS  •  24/7 SPORTS NEWS',font=fnt(23),fill=ACID); ps=MEDIA/f'{story_id}-story.jpg'; st.save(ps,quality=94,optimize=True); return slides,ps

def next_id(headline):
    nums=[]
    for p in QUEUE.glob('*.json'):
        m=re.match(r'(\d+)-',p.name)
        if m:nums.append(int(m.group(1)))
    n=max(nums,default=0)+1; slug=re.sub(r'[^a-z0-9]+','-',headline.lower()).strip('-')[:55] or 'story'; return f'{n:03d}-{slug}'

def main():
    if not os.environ.get('OPENAI_API_KEY'): raise RuntimeError('OPENAI_API_KEY is required. Crash Out Sports refuses to publish without verification and owned artwork.')
    cands=[c for c in parse_feed() if c['id'] not in existing() and c['link'] not in existing()]
    if not cands: print('Sports feed: no new candidates.'); return
    choice=choose(cands); src=choice['source_item']; headline=clean(choice['headline']); story=clean(choice['story']); caption=clean(choice['caption']); label=clean(choice.get('source_label') or 'Verified reporting')
    if not headline or not story:raise RuntimeError('AI returned empty editorial copy')
    verification_sources=choice.get('verification_sources') or []
    verified_urls=[]
    for source in verification_sources:
        url=clean(source.get('url') if isinstance(source,dict) else '')
        if url.startswith('https://') and url not in verified_urls: verified_urls.append(url)
    if len(verified_urls)<2: raise RuntimeError('Two independent credible verification sources are required')
    reference_url,reference_data=discover_source_image(src); person=clean(choice.get('featured_person')); handle=clean(choice.get('instagram_handle')); profile=clean(choice.get('instagram_profile_url')); handle=handle if handle.startswith('@') else ''
    if person and (not handle or 'instagram.com/' not in profile):raise RuntimeError('Featured-person Instagram handle was not verified')
    person_label=f'{person.upper()}  {handle}' if person and handle else ''; extra_context=clean(choice.get('extra_context')); pages=3 if str(choice.get('carousel_pages') or 2).strip()=='3' and extra_context else 2
    print('AI selected:',headline); print('Using source visual reference:',reference_url); print('Generating source-grounded comic art...'); art=generate_art(choice['visual_scene'],headline,reference_data); sid=next_id(headline); slides,ps=assets(sid,headline,story,art,label,person_label,pages,extra_context); url=src['link']; identity_line=f'\n\n{person} ({handle})' if person_label else ''
    source_lines='\n'.join(f'- {clean(s.get("name") or "Source")}: {clean(s.get("url"))}' for s in verification_sources if isinstance(s,dict))
    item={'id':sid,'status':'ready','brand':'Crash Out Sports','league':clean(choice.get('league')),'story_type':clean(choice.get('story_type')),'confirmation_status':clean(choice.get('confirmation_status')),'verification_source_count':len(verified_urls),'ai_generated_art':True,'visual_asset_type':'ai_original_editorial_art_from_event_reference','visual_asset_rights':'owned','created_at':datetime.now(timezone.utc).isoformat(),'source':label,'source_urls':verified_urls,'source_url':url,'source_guid':src['id'],'source_title':src['title'],'source_published_at':src['published'],'story_fingerprint':re.sub(r'[^a-z0-9]+',' ',headline.lower()).strip(),'headline':headline,'body':story,'caption':f'{caption}{identity_line}\n\nSources:\n{source_lines}\n\n#CrashOutSports #SportsNews','threads_text':f'{headline}{identity_line}\n\n{story}\n\n#CrashOutSports','featured_person':person,'person_instagram_handle':handle,'person_handle_verified':bool(handle),'person_handle_verified_url':profile,'displayed_person_label':person_label,'visual_prompt':choice['visual_scene'],'slides':[str(p.relative_to(ROOT)) for p in slides],'carousel_page_count':len(slides),'story':str(ps.relative_to(ROOT)),'media_urls':[],'source_image_url':reference_url,'source_photo_used':True,'source_image_role':'factual visual reference only; final art is a materially redrawn original editorial illustration'}
    (QUEUE/f'{sid}.json').write_text(json.dumps(item,indent=2)+'\n'); print('Created:',sid)
if __name__=='__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        print('Crash Out Sports pipeline failed closed; nothing was published.')
        raise
