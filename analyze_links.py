import sys, csv, json, time, urllib.request, urllib.error
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

class LinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            for k,v in attrs:
                if k.lower() == 'href' and v: self.links.append(v)

def normalize_url(url):
    p=urlparse(url)._replace(fragment=''); netloc=p.netloc
    if p.scheme=='http' and netloc.endswith(':80'): netloc=netloc[:-3]
    if p.scheme=='https' and netloc.endswith(':443'): netloc=netloc[:-4]
    return urlunparse(p._replace(netloc=netloc))

def is_http_url(url): return urlparse(url).scheme.lower() in ('http','https')

def is_internal(url, domain):
    h=(urlparse(url).hostname or '').lower()
    return h == domain or h.endswith('.'+domain)

def fetch_url(url, timeout=10):
    start=time.perf_counter()
    req=urllib.request.Request(url, headers={'User-Agent':'PyWebsiteAnalytics/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body=r.read(); elapsed=time.perf_counter()-start
            return {'status':r.status,'latency':elapsed,'bytes':len(body),'content_type':r.headers.get('Content-Type',''),'body':body,'error':None}
    except urllib.error.HTTPError as e:
        elapsed=time.perf_counter()-start
        try: body=e.read()
        except Exception: body=b''
        return {'status':e.code,'latency':elapsed,'bytes':len(body),'content_type':e.headers.get('Content-Type','') if e.headers else '','body':body,'error':str(e)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {'status':None,'latency':time.perf_counter()-start,'bytes':0,'content_type':'','body':b'','error':str(e)}

def extract_links(page_url, html):
    parser=LinkParser()
    try: parser.feed(html)
    except Exception: return []
    result=[]
    for href in parser.links:
        if href.startswith(('mailto:','tel:','javascript:','data:')): continue
        u=urljoin(page_url,href)
        if is_http_url(u):
            u=normalize_url(u)
            if u not in result: result.append(u)
    return result

class WebsiteAnalyzer:
    def __init__(self,start_url,max_pages=100,timeout=10):
        self.start_url=normalize_url(start_url); p=urlparse(self.start_url)
        if not p.hostname: raise ValueError('Invalid URL')
        self.base_domain=p.hostname.lower(); self.max_pages=max_pages; self.timeout=timeout
        self.visited_pages=set(); self.checked_links=set(); self.queue=deque(); self.results=[]
    def check_and_record(self,url):
        if url in self.checked_links: return None,None
        self.checked_links.add(url); internal=is_internal(url,self.base_domain)
        response=fetch_url(url,self.timeout)
        result={'link':url,'type':'Internal' if internal else 'External','status':response['status'],'latency':response['latency'],'bytes':response['bytes'],'content_type':response['content_type'],'broken':response['status'] is None or response['status']>=400}
        self.results.append(result); return result,response
    def crawl(self):
        self.queue.append(self.start_url)
        while self.queue and len(self.visited_pages)<self.max_pages:
            current=self.queue.popleft()
            if current in self.visited_pages: continue
            self.visited_pages.add(current)
            print(f'\rFetching {current} ...',end='',flush=True)
            result,response=self.check_and_record(current)
            if not result: continue
            if response['status'] is not None and response['status']<400 and 'text/html' in response['content_type'].lower():
                html=response['body'].decode('utf-8',errors='ignore')
                for link in extract_links(current,html):
                    if is_internal(link,self.base_domain):
                        if link not in self.visited_pages and link not in self.queue: self.queue.append(link)
                    else:
                        print(f'\rChecking external link {link} ...',end='',flush=True); self.check_and_record(link)
        print()
    def summary(self):
        return {'total_links':len(self.results),'internal_links':sum(r['type']=='Internal' for r in self.results),'external_links':sum(r['type']=='External' for r in self.results),'broken_links':sum(r['broken'] for r in self.results),'max_latency':max((r['latency'] for r in self.results),default=0),'max_bytes':max((r['bytes'] for r in self.results),default=0)}

def print_report(a):
    s=a.summary(); print('\nSummary report:'); print(f"Total Links: {s['total_links']}, Internal: {s['internal_links']}, External: {s['external_links']}, Broken: {s['broken_links']}"); print(f"Max latency: {s['max_latency']*1000:.0f} ms, Max bytes transferred: {s['max_bytes']:,} bytes\n")
    print('Detailed report:'); print(f"{'Link':<55} {'E/I':<4} {'Response':<9} {'Latency':<10} {'Bytes':<12} Content-Type")
    print('-'*125)
    for r in a.results:
        link=r['link'] if len(r['link'])<=52 else r['link'][:49]+'...'; status=r['status'] if r['status'] is not None else 'ERR'; ct=r['content_type'] or '-'; ct=ct[:20]
        print(f"{link:<55} {('I' if r['type']=='Internal' else 'E'):<4} {str(status):<9} {r['latency']*1000:>7.0f} ms {r['bytes']:<12,} {ct}")

def save_json(filename,a):
    s=a.summary(); report={'url':a.start_url,'summary':{'total_links':s['total_links'],'internal_links':s['internal_links'],'external_links':s['external_links'],'broken_links':s['broken_links'],'max_latency_ms':s['max_latency']*1000,'max_bytes':s['max_bytes']},'links':[]}
    for r in a.results: report['links'].append({'link':r['link'],'type':r['type'],'status':r['status'],'latency_ms':r['latency']*1000,'bytes':r['bytes'],'content_type':r['content_type'],'broken':r['broken']})
    with open(filename,'w',encoding='utf-8') as f: json.dump(report,f,indent=4)

def save_csv(filename,a):
    fields=['link','type','status','latency_ms','bytes','content_type','broken']
    with open(filename,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in a.results: w.writerow({'link':r['link'],'type':r['type'],'status':r['status'],'latency_ms':r['latency']*1000,'bytes':r['bytes'],'content_type':r['content_type'],'broken':r['broken']})

def main():
    if len(sys.argv)<2: print('Usage: python analyze_links.py URL [--to=json|csv]'); sys.exit(1)
    url=sys.argv[1]; fmt='terminal'
    for arg in sys.argv[2:]:
        if arg.startswith('--to='): fmt=arg.split('=',1)[1].lower()
    if fmt not in ('terminal','json','csv'): print('Error: output format must be json or csv'); sys.exit(1)
    if urlparse(url).scheme not in ('http','https'): print('Error: URL must start with http:// or https://'); sys.exit(1)
    print(f'Starting website analysis for: {url}')
    a=WebsiteAnalyzer(url,max_pages=100,timeout=10)
    try: a.crawl()
    except KeyboardInterrupt: print('\nCrawling interrupted.')
    if fmt=='json': save_json('report.json',a); print('JSON report generated: report.json')
    elif fmt=='csv': save_csv('report.csv',a); print('CSV report generated: report.csv')
    else: print_report(a)

if __name__=='__main__': main()
