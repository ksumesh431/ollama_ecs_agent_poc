import boto3
import json
import urllib.request
import os
from datetime import datetime, timezone, timedelta
from fastmcp import FastMCP

mcp = FastMCP("AWS Custom MCP Server")


def get_aws_region() -> str:
    """
    Fetch region from EC2 IMDSv2. Falls back to AWS_DEFAULT_REGION
    env var if metadata is unreachable.
    """
    try:
        # Step 1: Get IMDSv2 token
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        )
        with urllib.request.urlopen(token_req, timeout=2) as resp:
            token = resp.read().decode()

        # Step 2: Get region using token
        region_req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers={"X-aws-ec2-metadata-token": token},
        )
        with urllib.request.urlopen(region_req, timeout=2) as resp:
            region = resp.read().decode()
            print(f"[startup] Auto-detected AWS region from IMDS: {region}")
            return region
    except Exception as e:
        fallback = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        print(f"[startup] IMDS region fetch failed ({e}), using fallback: {fallback}")
        return fallback


# Detect region once at startup
AWS_REGION = get_aws_region()


def get_boto3_client(service: str):
    """
    Returns a boto3 client using the region detected at startup.
    Credentials are auto-fetched from the EC2 instance profile.
    """
    return boto3.client(service, region_name=AWS_REGION)


@mcp.tool()
def list_ecs_clusters() -> str:
    """List all ECS clusters in the AWS account."""
    ecs = get_boto3_client("ecs")
    arns = ecs.list_clusters().get("clusterArns", [])
    if not arns:
        return "No ECS clusters found."
    clusters = ecs.describe_clusters(clusters=arns)["clusters"]
    result = [
        {
            "clusterName": c["clusterName"],
            "status": c["status"],
            "activeServicesCount": c["activeServicesCount"],
            "runningTasksCount": c["runningTasksCount"],
            "pendingTasksCount": c["pendingTasksCount"],
        }
        for c in clusters
    ]
    return json.dumps(result, indent=2)


@mcp.tool()
def list_ecs_services(cluster_name: str) -> str:
    """List all services in a given ECS cluster."""
    ecs = get_boto3_client("ecs")
    arns = ecs.list_services(cluster=cluster_name).get("serviceArns", [])
    if not arns:
        return f"No services found in cluster: {cluster_name}"
    services = ecs.describe_services(
        cluster=cluster_name, services=arns
    )["services"]
    result = [
        {
            "serviceName": s["serviceName"],
            "status": s["status"],
            "desiredCount": s["desiredCount"],
            "runningCount": s["runningCount"],
            "pendingCount": s["pendingCount"],
            "launchType": s.get("launchType", "N/A"),
            "capacityProviderStrategy": s.get("capacityProviderStrategy", []),
        }
        for s in services
    ]
    return json.dumps(result, indent=2)


@mcp.tool()
def get_ecs_service_details(cluster_name: str, service_name: str) -> str:
    """
    Get full details of a specific ECS service including task counts,
    deployments, and events.
    """
    ecs = get_boto3_client("ecs")
    response = ecs.describe_services(
        cluster=cluster_name, services=[service_name]
    )
    services = response.get("services", [])
    if not services:
        return f"Service '{service_name}' not found in cluster '{cluster_name}'"
    s = services[0]
    result = {
        "serviceName": s["serviceName"],
        "status": s["status"],
        "desiredCount": s["desiredCount"],
        "runningCount": s["runningCount"],
        "pendingCount": s["pendingCount"],
        "launchType": s.get("launchType", "N/A"),
        "capacityProviderStrategy": s.get("capacityProviderStrategy", []),
        "deployments": [
            {
                "status": d["status"],
                "desiredCount": d["desiredCount"],
                "runningCount": d["runningCount"],
                "pendingCount": d["pendingCount"],
                "createdAt": str(d["createdAt"]),
                "updatedAt": str(d["updatedAt"]),
            }
            for d in s.get("deployments", [])
        ],
        "recentEvents": [
            {
                "createdAt": str(e["createdAt"]),
                "message": e["message"],
            }
            for e in s.get("events", [])[:10]
        ],
    }
    return json.dumps(result, default=str, indent=2)


@mcp.tool()
def get_asg_for_ecs_cluster(cluster_name: str) -> str:
    """
    Find the Auto Scaling Group linked to an ECS cluster by resolving
    the capacity provider -> ASG ARN chain. Works for EC2 launch type
    with ASG capacity providers.
    """
    ecs = get_boto3_client("ecs")
    asg_client = get_boto3_client("autoscaling")

    cluster_resp = ecs.describe_clusters(
        clusters=[cluster_name],
        include=["ATTACHMENTS"]
    )
    clusters = cluster_resp.get("clusters", [])
    if not clusters:
        return f"Cluster '{cluster_name}' not found."

    capacity_providers = clusters[0].get("capacityProviders", [])
    if not capacity_providers:
        return f"No capacity providers found on cluster '{cluster_name}'."

    cp_resp = ecs.describe_capacity_providers(
        capacityProviders=capacity_providers
    )

    results = []
    for cp in cp_resp["capacityProviders"]:
        asg_arn = (
            cp.get("autoScalingGroupProvider", {})
            .get("autoScalingGroupArn", "")
        )
        if not asg_arn:
            continue

        asg_name = asg_arn.split("/")[-1]
        asg_resp = asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )
        for group in asg_resp["AutoScalingGroups"]:
            results.append({
                "capacityProviderName": cp["name"],
                "autoScalingGroupName": group["AutoScalingGroupName"],
                "desiredCapacity": group["DesiredCapacity"],
                "minSize": group["MinSize"],
                "maxSize": group["MaxSize"],
                "instances": [
                    {
                        "instanceId": i["InstanceId"],
                        "lifecycleState": i["LifecycleState"],
                        "healthStatus": i["HealthStatus"],
                        "availabilityZone": i["AvailabilityZone"],
                    }
                    for i in group.get("Instances", [])
                ],
            })

    if not results:
        return "No ASG-backed capacity providers found."
    return json.dumps(results, default=str, indent=2)


@mcp.tool()
def get_asg_scaling_events(asg_name: str, hours_back: int = 1) -> str:
    """
    Get scaling activities for an Auto Scaling Group in the last N hours.
    Includes instance launches, terminations, draining, and unhealthy
    instance replacements.
    """
    asg_client = get_boto3_client("autoscaling")
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    response = asg_client.describe_scaling_activities(
        AutoScalingGroupName=asg_name,
        MaxRecords=100,
    )
    activities = [
        {
            "description": a["Description"],
            "statusCode": a["StatusCode"],
            "statusMessage": a.get("StatusMessage", ""),
            "startTime": str(a["StartTime"]),
            "endTime": str(a.get("EndTime", "In Progress")),
            "cause": a.get("Cause", ""),
        }
        for a in response.get("Activities", [])
        if a["StartTime"] >= since
    ]

    if not activities:
        return (
            f"No scaling activities found for ASG '{asg_name}' "
            f"in the last {hours_back} hour(s)."
        )
    return json.dumps(activities, default=str, indent=2)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
