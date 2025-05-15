from res_ads.utils.getredis import get_redis_client
import json

k = "realestate_spider:testurls"
# url = "https://www.realestate.com.au/property-house-vic-tarneit-148005336"
url = "https://www.realestate.com.au/property-house-vic-altona+meadows-147997576"
data = {"url":url, "meta":{}}
r = get_redis_client()
r.lpush(k, json.dumps(data))