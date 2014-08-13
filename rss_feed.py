from datetime import datetime
import logging

from config import CHATROOM_PRESENCE
from feedparser import parse

from BeautifulSoup import BeautifulSoup

from errbot.utils import get_sender_username

# Backward compatibility
from errbot.version import VERSION
from errbot.utils import version2array
if version2array(VERSION) >= [1,6,0]:
    from errbot import botcmd, BotPlugin
else:
    from errbot.botplugin import BotPlugin
    from errbot.jabberbot import botcmd


__author__ = 'atalyad'


def get_item_date(rss_item):
    time = getattr(rss_item, 'published_parsed', None)
    time = time or getattr(rss_item, 'updated_parsed', None)
    if time:
        return datetime(time.tm_year,
                        time.tm_mon,
                        time.tm_mday,
                        time.tm_hour,
                        time.tm_min,
                        time.tm_sec)
    return datetime.now()

DEFAULT_POLL_INTERVAL = 1800

class Subscription(object):
    def __init__(self, url, name, username=None):
        self.name = name
        self.url = url
        self.username = username if username else ''
        self.last_timestamp = datetime.now()

    def has_new_items(self):
        if self.get_new_item(mark_read=False):
            return True
        return False

    def get_new_item(self, mark_read=False):
        feed = parse(self.url)
        if feed['entries']:
            latest = feed['entries'][0]
            latest_timestamp = get_item_date(latest)
            if latest_timestamp > self.last_timestamp:
                if mark_read:
                    self.last_timestamp = latest_timestamp
                return latest
        return None


class RSSFeedPlugin(BotPlugin):
    min_err_version = '1.4.0' # it needs the new polling feature

    def get_configuration_template(self):
        return {'POLL_INTERVAL': DEFAULT_POLL_INTERVAL}

    def configure(self, configuration):
        if configuration:
            if type(configuration) != dict:
                raise Exception('Wrong configuration type')

            if not configuration.has_key('POLL_INTERVAL'):
                raise Exception('Wrong configuration type, it should contain POLL_INTERVAL')
            if len(configuration) > 1:
                raise Exception('What else did you try to insert in my config ?')
            try:
                int(configuration['POLL_INTERVAL'])
            except:
                raise Exception('POLL_INTERVAL must be an integer')
        super(RSSFeedPlugin, self).configure(configuration)

    def get_subscriptions(self, username=None):
        if username:
            user_subscriptions = self.get('user_subscriptions', {})
            return user_subscriptions.get(username, {}).values()
        
        return self.get('group_subscriptions', {}).values()

    def get_all_subscriptions(self):
        user_subscriptions = self.get('user_subscriptions', {})
        subscriptions = [item for subscriptions in user_subscriptions.values() for item in subscriptions.values()]
        subscriptions.extend(self.get('group_subscriptions', {}).values())
        return subscriptions

    def add_subscription(self, url, name, username=None):
        new = Subscription(url, name, username)
        if username:
            user_subscriptions = self.get('user_subscriptions', {})
            if username not in user_subscriptions:
                user_subscriptions[username] = {}
            user_subscriptions[username][name] = new
            self['user_subscriptions'] = user_subscriptions
        else:
            group_subscriptions = self.get('group_subscriptions', {})
            group_subscriptions[name] = new
            self['group_subscriptions'] = group_subscriptions


    def remove_subscription(self, name, username=None):
        if username:
            user_subscriptions = self.get('user_subscriptions', {})
            removed = user_subscriptions.get(username, {}).pop(name, None)
            self['user_subscriptions'] = user_subscriptions
            return removed
        group_subscriptions = self.get('group_subscriptions', {})
        removed = group_subscriptions.pop(name, None)
        self['group_subscriptions'] = group_subscriptions
        return removed

    def update_subscription(self, subscription):
        if subscription.username:
            user_subscriptions = self.get('user_subscriptions', {})
            user_subscriptions.get(subscription.username, {})[subscription.name] = subscription
            self['user_subscriptions'] = user_subscriptions
        else:
            group_subscriptions = self.get('group_subscriptions', {})
            group_subscriptions[subscription.name] = subscription
            self['group_subscriptions'] = group_subscriptions

    def clean_html(self, html_item):
        soup = BeautifulSoup(html_item)
        text_parts = soup.findAll(text=True)
        text = ''.join(text_parts)
        return text

    def send_news(self, username=None):
        """
        Go through RSS subscriptions, check if there's a new update and send it to the chat.
        """
        logging.info('Polling rss feeds')
        if username:
            subscriptions = self.get_subscriptions(username=username)
        else:
            subscriptions = self.get_all_subscriptions()
        canary = False
        for subscription in subscriptions:
            item = subscription.get_new_item(mark_read=True)
            if item:
                canary = True
                item_date = get_item_date(item)
                recipient = getattr(subscription, 'username', None) or CHATROOM_PRESENCE[0]
                message_type = 'chat' if subscription.username else 'groupchat'
                self.send(recipient, '%s News from %s:\n%s' % (item_date, subscription.name, self.clean_html(item.summary)), message_type=message_type)
                self.send(recipient, '\n%s\n' % str(item.link), message_type=message_type)
                self.update_subscription(subscription)
        if not canary and username:
            logging.info('No new news')
            self.send(username, 'No new news.\n', message_type='chat')

    def activate(self):
        super(RSSFeedPlugin, self).activate()
        self.start_poller(self.config['POLL_INTERVAL'] if self.config else DEFAULT_POLL_INTERVAL, self.send_news)

    @botcmd(split_args_with=' ')
    def rss_add(self, mess, args):
        """
        Add a feed: !rss add feed_url feed_nickname
        The feed will be added to your personal list if you are in a 1-1 chat with the bot,
        or the group list if you are in a group chat room.
        """
        if len(args) < 2:
            return 'Please supply a feed url and a nickname'

        feed_url = args[0].strip()

        feed_name = ''
        for i in range(1, len(args)):
            feed_name += args[i] + ' '
        feed_name = feed_name.strip()

        username = None
        if mess.getType() == 'chat' or not CHATROOM_PRESENCE:
            username = mess.getFrom().getStripped()

        if feed_name in [subscription.name for subscription in self.get_subscriptions(username=username)]:
            return 'this feed already exists'
        self.add_subscription(feed_url, feed_name, username)
        return 'Feed %s added as %s for %s' % (feed_url, feed_name, username if username else 'group chat')


    @botcmd
    def rss_remove(self, mess, args):
        """
        Remove a feed: !rss remove feed_nickname
        """
        if not args:
            return 'Please supply a feed nickname'
        feed_name = args.strip()

        username = mess.getFrom().getStripped()
        removed = self.remove_subscription(feed_name, username=username)
        if not removed:
            removed = self.remove_subscription(feed_name, username=None)
        if not removed:
            return 'Sorry.. unknown feed...'
        return 'Feed %s was successfully removed.' % removed.name


    @botcmd(split_args_with=' ')
    def rss_feeds(self, mess, args):
        """
        Display all active feeds with last update date
        """
        username = None
        if mess.getType() == 'chat':
            username = mess.getFrom().getStripped()

        subscriptions = self.get_subscriptions(username=username)
        ans = ''
        for subscription in subscriptions:
            ans += '\n%s  last updated: %s (from %s)' % (subscription.name, subscription.last_timestamp, subscription.url)

        return ans


    @botcmd(admin_only=True, split_args_with=' ')
    def rss_clearfeeds(self, mess, args):
        """ WARNING : Deletes all existing feeds
        """
        self['user_subscriptions'] = {}
        self['group_subscriptions'] = {}
        return 'all rss feeds were removed'

    @botcmd
    def rss_news(self, mess, args):
        """
        Go through RSS subscriptions, check if there's a new update and send it to the chat.
        """
        username = None
        if mess.getType() == 'chat':
            username = mess.getFrom().getStripped()
        return self.send_news(username=username)
