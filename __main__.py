#! /usr/bin/python3

# requirements
import bottle
import peewee
import tweepy
import yaml

# std
import datetime
import hashlib
import itertools
import os.path
import sys
import threading
import time
import urllib.request
import urllib.error

# local
import dbsetup
models = dbsetup.models

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONF    = yaml.load(open(sys.argv[1]))

def tweepy_api_init():
  ah = tweepy.OAuthHandler(CONF['consumer']['key'], CONF['consumer']['secret'])
  ah.set_access_token(CONF['access_token']['key'], CONF['access_token']['secret'])
  return tweepy.API(ah)

api = tweepy_api_init()
db  = getattr(peewee, CONF['database']['type'])(CONF['database']['path'], **CONF['database'].get('args', {}))
dbsetup.init(db)

def fetch_tweets():
  return getattr(api, CONF['target']['type'])(**CONF['target']['args'])

def fetch_media(t):
  ret = []
  if hasattr(t, 'retweeted_status'):
    t = t.retweeted_status
  for m in reversed(getattr(t, 'extended_entities', t.entities).get('media', [])):
    cont = None
    while True:
      try:
        with urllib.request.urlopen(m['media_url_https'] + ':orig') as res:
          cont = res.read()
        break
      except urllib.error.HTTPError as e:
        print(sys.exc_info()[0], file = sys.stderr)
        if e.code // 100 == 4:
          break
        time.sleep(1)
    if cont is None:
      continue
    h = hashlib.md5()
    h.update(cont)
    fn = '{0}{1:02d}{2:02d}{3:02d}{4:02d}{5:02d}.{6}.{7}'.format(
        t.created_at.year,
        t.created_at.month,
        t.created_at.day,
        t.created_at.hour,
        t.created_at.minute,
        t.created_at.second,
        h.hexdigest(),
        m['media_url_https'].split('.')[-1],
      )
    dest = os.path.join(CONF['images']['savedest'], fn)
    if not os.path.exists(dest):
      with open(dest, 'wb') as f:
        f.write(cont)
    ret.append(fn)
  return ret

def auth(username, password):
  return username == CONF['auth']['user'] and password == CONF['auth']['pass']

class FetchThread(threading.Thread):
  def run(self):
    while True:
      try:
        for t in fetch_tweets():
          if len(models.Tweet.select().where(models.Tweet.tid == t.id)) == 0:
            item = models.Tweet(
                tid         = t.id,
                screen_name = t.author.screen_name,
                text        = t.text if not hasattr(t, 'retweeted_status') else 'RT @{0}: {1}'.format(t.retweeted_status.author.screen_name, t.retweeted_status.text),
                created_at  = t.created_at
              )
            item.save()
            for fn in fetch_media(t):
              img = models.Image(tweet = item, filename = fn)
              img.save()
        db.commit()
        print('COMMIT', file = sys.stderr)
        time.sleep(90)
      except tweepy.error.TweepError:
        print(sys.exc_info()[0], file = sys.stderr)
        time.sleep(10)

FetchThread().start()

@bottle.route("/")
@bottle.auth_basic(auth)
def index():
  imgs = models.Image.select().order_by(models.Image.id.desc()).limit(100)
  imgs = [(img.id, CONF['server']['cont'] + '/' + img.filename, img.tweet.tid, img.tweet.screen_name, img.tweet.text) for img in imgs]
  return bottle.jinja2_template(os.path.join(APP_DIR, 'templates', 'index.jinja2'), imgs = imgs, appname = CONF['appname'])

@bottle.route(CONF['server']['cont'] + '/<filename:path>')
@bottle.auth_basic(auth)
def cont(filename):
  return bottle.static_file(filename, root = CONF['images']['savedest'])

@bottle.route('/static/<filename:path>')
@bottle.auth_basic(auth)
def static(filename):
  return bottle.static_file(filename, root = os.path.join(APP_DIR, 'static'))

@bottle.route('/list.json')
@bottle.auth_basic(auth)
def list():
  maxid = bottle.request.query.get('maxid', None)
  imgs = models.Image.select().order_by(models.Image.id.desc()).limit(100)
  if maxid is not None and maxid.isdecimal():
    imgs = imgs.where(models.Image.id <= maxid)
  imgs = [
      {
        'id': img.id,
        'src': os.path.join(CONF['server']['cont'], img.filename),
        'href': 'https://twitter.com/{1}/status/{0}'.format(img.tweet.tid, img.tweet.screen_name),
        'alt': img.tweet.text,
      } for img in imgs]
  return {'imgs': imgs}

bottle.run(**CONF['server'].get('args'))
