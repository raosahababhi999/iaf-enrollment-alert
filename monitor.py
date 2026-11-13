import os, json, hashlib, html, re, sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
import io

PAGES = ['https://iafrecruitment.edcil.co.in/airmen/', 'https://iafrecruitment.edcil.co.in/airmen/psl.html']
STATE = Path('state.json')
TERMS = ('psl','provisional selection','selection list','enrolment','enrollment','intake','2027','0127')
PATTERNS = [re.compile(r'\bintake\s*0?1\s*[/\-]\s*2027\b', re.I), re.compile(r'\b01\s*[/\-]\s*2027\b', re.I)]
TIMEOUT = (15, 45)

def http():
    s = requests.Session()
    s.headers.update({'User-Agent':'IAF-Enrolment-Alert-Monitor/1.0','Accept':'text/html,application/pdf,*/*'})
    return s

def telegram(s, message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat: raise RuntimeError('Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions Secrets')
    r = s.post('https://api.telegram.org/bot'+token+'/sendMessage', json={'chat_id':chat,'text':message,'parse_mode':'HTML','disable_web_page_preview':True}, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.json().get('ok'): raise RuntimeError('Telegram API rejected sendMessage')

def discover(s):
    links, errors, success = {}, [], 0
    for page in PAGES:
        try:
            r=s.get(page,timeout=TIMEOUT); r.raise_for_status()
            if 'html' not in r.headers.get('Content-Type','').lower() and not r.text.lstrip().startswith('<'): raise RuntimeError('Response is not HTML')
            success += 1
            soup=BeautifulSoup(r.text,'html.parser')
            for a in soup.find_all('a',href=True):
                url=urljoin(r.url,a['href'].strip()); label=' '.join(a.get_text(' ',strip=True).split()) or 'Official PDF'
                if urlparse(url).path.lower().endswith('.pdf') and any(t in (label+' '+url).lower() for t in TERMS):
                    links[url]={'url':url,'label':label,'source_page':page}
        except Exception as e: errors.append(page+': '+str(e))
    if not success: raise RuntimeError('All official pages failed: '+' | '.join(errors))
    return links,errors

def inspect(s,item):
    r=s.get(item['url'],timeout=TIMEOUT); r.raise_for_status(); data=r.content
    if len(data)>35*1024*1024: raise RuntimeError('PDF larger than 35 MB')
    if not data.startswith(b'%PDF') and 'pdf' not in r.headers.get('Content-Type','').lower(): raise RuntimeError('URL did not return a PDF')
    text=''
    try: text='\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(data),strict=False).pages[:30])
    except Exception: pass
    hay=(item['label']+' '+item['url']+' '+text).lower()
    return {**item,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'intake_match':any(p.search(hay) for p in PATTERNS),'text_extractable':bool(text.strip()),'checked_at':datetime.now(timezone.utc).isoformat()}

def send_doc(s,doc,status):
    label=html.escape(doc['label']); url=html.escape(doc['url'],quote=True); source=html.escape(doc['source_page'],quote=True)
    match='Intake 01/2027 text matched' if doc['intake_match'] else 'Intake 01/2027 not confirmed in extracted text'
    telegram(s,'🚨 <b>IAF INTAKE 01/2027 ALERT</b>\n\n<b>'+status+'</b>\nDocument: '+label+'\n'+match+'\nSize: '+str(doc['bytes'])+' bytes\n\n<a href="'+url+'">Open official PDF</a>\n<a href="'+source+'">Source page</a>')

def main():
    s=http(); state=json.loads(STATE.read_text()) if STATE.exists() else {'initialized':False,'documents':{}}
    old=state.get('documents',{}); links,page_errors=discover(s); current={}; pdf_errors=[]
    for url,item in links.items():
        try: current[url]=inspect(s,item)
        except Exception as e: pdf_errors.append(url+': '+str(e))
    if links and not current: raise RuntimeError('PDF links found but none could be checked: '+' | '.join(pdf_errors))
    first=not state.get('initialized'); new=[]; changed=[]
    if not first:
        for url,doc in current.items():
            prior=old.get(url)
            if prior is None: new.append(doc)
            elif prior.get('sha256')!=doc['sha256']: changed.append(doc)
    merged=dict(old); merged.update(current)
    state.update({'initialized':True,'documents':merged,'last_checked_at':datetime.now(timezone.utc).isoformat(),'last_error':' | '.join(page_errors+pdf_errors) or None})
    STATE.write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n')
    if first: telegram(s,'✅ <b>IAF Enrolment Alert monitor is active</b>\nInitial baseline saved: '+str(len(current))+' relevant PDF(s). New or changed PDFs will alert on later checks.')
    else:
        for d in new: send_doc(s,d,'NEW PDF DETECTED')
        for d in changed: send_doc(s,d,'PDF CONTENT CHANGED')
    if page_errors or pdf_errors:
        details='\n'.join('• '+html.escape(x[:500]) for x in (page_errors+pdf_errors)[:5])
        telegram(s,'⚠️ <b>Partial check warning</b>\nSome pages/PDFs could not be checked. This is not proof that no update exists.\n'+details)
    print('Links:',len(links),'Checked:',len(current),'New:',len(new),'Changed:',len(changed),'Errors:',len(page_errors)+len(pdf_errors))

if __name__=='__main__':
    try: main()
    except Exception as e:
        print('MONITOR FAILED:',repr(e),file=sys.stderr)
        try: telegram(http(),'❌ <b>IAF monitor failed</b>\n'+html.escape(str(e))[:2000])
        except Exception as te: print('Telegram failure alert failed:',repr(te),file=sys.stderr)
        raise
