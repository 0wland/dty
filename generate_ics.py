from icalendar import Calendar, Event
from datetime import datetime, date, timedelta
import yaml

with open('data.yaml', 'r') as f:
    data = yaml.safe_load(f)

cal = Calendar()
cal.add('prodid', '-//DTY Calendar//SK//1.0')
cal.add('version', '2.0')
cal.add('X-WR-CALNAME', data['calendar']['name'])

for event_data in data['events']:
    start = date.fromisoformat(event_data['start_date'])
    end = date.fromisoformat(event_data['end_date'])

    event = Event()
    event.add('dtstart', start)
    event.add('dtend', end + timedelta(days=1))
    event.add('TRANSP', 'TRANSPARENT')
    event.add('summary', event_data['summary'])
    event.add('description', f"{event_data['summary']} dty")
    event.add('uid', f"dty-{event_data['summary'].lower().replace(' ', '-')}-{start.isoformat()}")

    reminder_date = start - timedelta(days=1)
    reminder = Event()
    reminder.add('dtstart', datetime(reminder_date.year, reminder_date.month, reminder_date.day, 12, 0))
    reminder.add('dtend', datetime(reminder_date.year, reminder_date.month, reminder_date.day, 12, 30))
    reminder.add('summary', f"🔔 REMINDER: {event_data['summary']}")
    reminder.add('description', f"{event_data['summary']} dty - začína zajtra!")
    reminder.add('TRANSP', 'TRANSPARENT')
    reminder.add('uid', f"dty-reminder-{event_data['summary'].lower()}-{reminder_date.isoformat()}")

    cal.add_component(reminder)
    cal.add_component(event)

with open('calendar.ics', 'wb') as f:
    f.write(cal.to_ical())
