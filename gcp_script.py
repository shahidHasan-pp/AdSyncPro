import os
import re
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
from datetime import datetime, timedelta

# Comprehensive scopes for AdSync Pro (all data including monetary)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
]

def parse_duration(iso_duration):
    """Convert ISO 8601 duration (PT5M20S) to seconds."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso_duration)
    if match:
        hours, minutes, seconds = map(int, match.groups(default=0))
        return hours * 3600 + minutes * 60 + seconds
    return 0

def get_adsync_pro_complete_report(video_id, content_owner_id=None, days_back=90):
    """Complete AdSync Pro report with ALL available metrics."""
    
    # Dynamic date range (last N days)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    # 1. OAuth 2.0 Authorization
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # Local testing only
    client_secrets_file = "gcp_client_secrets.json"
    
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        client_secrets_file, SCOPES)
    credentials = flow.run_local_server(port=0)
    
    # 2. Build API Clients
    youtube_data = googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
    youtube_analytics = googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=credentials)
    
    # 3. Video Metadata (ALL parts for complete info)
    video_request = youtube_data.videos().list(
        part="snippet,contentDetails,statistics,status,player,topicDetails",
        id=video_id
    )
    video_response = video_request.execute()
    
    if not video_response['items']:
        print("❌ Video not found.")
        return
    
    video_item = video_response['items'][0]
    metadata = {
        "title": video_item['snippet']['title'],
        "description": video_item['snippet']['description'][:100] + "...",
        "channel_id": video_item['snippet']['channelId'],
        "channel_title": video_item['snippet']['channelTitle'],
        "duration_iso": video_item['contentDetails']['duration'],
        "duration_seconds": parse_duration(video_item['contentDetails']['duration']),
        "published_at": video_item['snippet']['publishedAt'],
        "view_count": video_item['statistics'].get('viewCount', 0),
        "like_count": video_item['statistics'].get('likeCount', 0),
        "comment_count": video_item['statistics'].get('commentCount', 0),
        "privacy_status": video_item['status']['privacyStatus']
    }
    
    # 4. COMPLETE Analytics Report (ALL core + ad metrics)
    ids_param = f"contentOwner=={content_owner_id}" if content_owner_id else "channel==MINE"
    
    analytics_request = youtube_analytics.reports().query(
        ids=ids_param,
        startDate=start_date,
        endDate=end_date,
        # ALL MAJOR METRICS from YouTube Analytics docs
        metrics=(
            "views,uniques,engagedViews,viewerPercentage,"
            "estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
            "estimatedRevenue,estimatedAdRevenue,adImpressions,monetizedPlaybacks,"
            "playbackBasedCpm,cpm,likes,dislikes,comments,shares,"
            "subscribersGained,subscribersLost,redViews,estimatedRedMinutesWatched,"
            "videoThumbnailImpressions,videoThumbnailImpressionsClickRate,"
            "cardsImpressions,cardsClickRate,endScreenClickRate"
        ),
        dimensions="video",
        filters=f"video=={video_id}",
        sort="-views",
        maxResults=10
    )
    analytics_response = analytics_request.execute()
    
    # 5. Ad Segment Analysis (Retention at 20% mark ~1:20 for 5min video)
    retention_request = youtube_analytics.reports().query(
        ids=ids_param,
        startDate=start_date,
        endDate=end_date,
        metrics="audienceWatchRatio,views",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={video_id}",
        maxResults=50
    )
    retention_response = retention_request.execute()
    
    # 6. DISPLAY COMPLETE REPORT
    print("🚀 ADSYNC PRO - COMPLETE VIDEO ANALYTICS REPORT")
    print("=" * 70)
    print(f"📹 Video: {metadata['title']}")
    print(f"👤 Creator: {metadata['channel_title']}")
    print(f"⏱️  Length: {metadata['duration_iso']} ({metadata['duration_seconds']}s)")
    print(f"📅 Published: {metadata['published_at']}")
    print(f"🔒 Privacy: {metadata['privacy_status']}")
    print()
    
    if "rows" in analytics_response:
        row = analytics_response["rows"][0]
        headers = [h["name"] for h in analytics_response["columnHeaders"]]
        stats = dict(zip(headers, row))
        
        # VIEWS & REACH
        print("👁️  VIEWS & REACH")
        print(f"  Total Views: {stats.get('views', 0):,}")
        print(f"  Unique Viewers: {stats.get('uniques', 0):,}")
        print(f"  Engaged Views: {stats.get('engagedViews', 0):,}")
        print(f"  Thumbnail Impressions: {stats.get('videoThumbnailImpressions', 0):,}")
        print(f"  Thumbnail CTR: {stats.get('videoThumbnailImpressionsClickRate', 0)}%")
        
        # WATCH TIME
        print("\n⏱️  WATCH TIME")
        print(f"  Total Minutes Watched: {stats.get('estimatedMinutesWatched', 0):,.0f}")
        print(f"  Avg View Duration: {stats.get('averageViewDuration', 0):.0f}s")
        print(f"  Avg % Watched: {stats.get('averageViewPercentage', 0)}%")
        
        # MONETIZATION/ADS (Business Owner Priority)
        print("\n💰 MONETIZATION & ADS")
        revenue = stats.get('estimatedRevenue', 0)
        print(f"  Estimated Revenue: ${revenue:.2f}")
        print(f"  Ad Impressions: {stats.get('adImpressions', 0):,}")
        print(f"  Monetized Playbacks: {stats.get('monetizedPlaybacks', 0):,}")
        print(f"  CPM: ${stats.get('cpm', 0):.2f}")
        
        # ENGAGEMENT
        print("\n❤️ ENGAGEMENT")
        print(f"  Likes: {stats.get('likes', 0):,} | Dislikes: {stats.get('dislikes', 0):,}")
        print(f"  Comments: {stats.get('comments', 0):,}")
        print(f"  Shares: {stats.get('shares', 0):,}")
        print(f"  Subs Gained: +{stats.get('subscribersGained', 0):,}")
        
        # CARDS/END SCREENS
        print("\n🎯 INTERACTIVE")
        print(f"  Cards Impressions: {stats.get('cardsImpressions', 0):,}")
        print(f"  Cards CTR: {stats.get('cardsClickRate', 0)}%")
        
        # AD SEGMENT ANALYSIS
        print("\n📊 AD SEGMENT (1:00-1:20) ANALYSIS")
        ad_retention = 0
        if "rows" in retention_response:
            for row in retention_response["rows"]:
                ratio, ratio_views = row
                if float(ratio) >= 0.2:  # 20% mark
                    ad_retention = ratio_views
                    break
        retention_pct = (ad_retention / stats.get('views', 1)) * 100
        print(f"  % Viewers reaching ad segment: {retention_pct:.1f}%")
        if retention_pct > 50:
            print("  ✅ Good retention through ad")
        else:
            print("  ⚠️  Low retention - optimize ad placement")
            
    else:
        print("❌ No analytics data available.")
    
    print("\n" + "=" * 70)
    print("💡 Pro Tip: Set CONTENT_OWNER_ID for multi-creator dashboard")

if __name__ == "__main__":
    # CONFIGURATION - Update these
    VIDEO_ID = "-xLfxNuW-xo"  # From content creator
    CONTENT_OWNER_ID = "ForeignerSuiteLover"  # From Partner Manager (optional)
    DAYS_BACK = 90  # Analytics period
    
    get_adsync_pro_complete_report(VIDEO_ID, CONTENT_OWNER_ID, DAYS_BACK)