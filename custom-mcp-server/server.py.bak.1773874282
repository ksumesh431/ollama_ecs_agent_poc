import boto3
import json
import urllib.request
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from fastmcp import FastMCP

mcp = FastMCP("AWS Custom MCP Server")


def get_aws_region() -> str:
    """
    Fetch region from EC2 IMDSv2. Falls back to AWS_DEFAULT_REGION
    env var if metadata is unreachable.
    """
    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        )
        with urllib.request.urlopen(token_req, timeout=2) as resp:
            token = resp.read().decode()

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


AWS_REGION = get_aws_region()


def get_boto3_client(service: str):
    """
    Returns a boto3 client using the region detected at startup.
    Credentials are auto-fetched from the EC2 instance profile.
    """
    return boto3.client(service, region_name=AWS_REGION)


def _json(data: Any) -> str:
    return json.dumps(data, default=str, indent=2)


def _paginate(client, operation_name: str, result_key: str, **kwargs):
    paginator = client.get_paginator(operation_name)
    results = []
    for page in paginator.paginate(**kwargs):
        results.extend(page.get(result_key, []))
    return results


def _get_cluster(ecs, cluster_name: str):
    resp = ecs.describe_clusters(clusters=[cluster_name], include=["ATTACHMENTS"])
    clusters = resp.get("clusters", [])
    if not clusters:
        return None
    return clusters[0]


def _get_service(ecs, cluster_name: str, service_name: str):
    resp = ecs.describe_services(cluster=cluster_name, services=[service_name])
    services = resp.get("services", [])
    if not services:
        return None
    return services[0]


def _resolve_asg_backing_cluster(cluster_name: str):
    ecs = get_boto3_client("ecs")
    asg_client = get_boto3_client("autoscaling")

    cluster = _get_cluster(ecs, cluster_name)
    if not cluster:
        return []

    capacity_providers = cluster.get("capacityProviders", [])
    if not capacity_providers:
        return []

    cp_resp = ecs.describe_capacity_providers(capacityProviders=capacity_providers)

    results = []
    for cp in cp_resp.get("capacityProviders", []):
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

        for group in asg_resp.get("AutoScalingGroups", []):
            instances = group.get("Instances", [])
            results.append({
                "capacityProviderName": cp["name"],
                "autoScalingGroupName": group["AutoScalingGroupName"],
                "desiredCapacity": group["DesiredCapacity"],
                "minSize": group["MinSize"],
                "maxSize": group["MaxSize"],
                "instanceCount": len(instances),
                "instancesInService": sum(1 for i in instances if i.get("LifecycleState") == "InService"),
                "instances": [
                    {
                        "instanceId": i["InstanceId"],
                        "lifecycleState": i["LifecycleState"],
                        "healthStatus": i["HealthStatus"],
                        "availabilityZone": i["AvailabilityZone"],
                    }
                    for i in instances
                ],
            })

    return results


def _extract_log_sources_from_task_definition(task_definition: dict):
    sources = []
    for container in task_definition.get("containerDefinitions", []):
        log_cfg = container.get("logConfiguration", {})
        options = log_cfg.get("options", {})
        if log_cfg.get("logDriver") != "awslogs":
            continue

        log_group = options.get("awslogs-group")
        stream_prefix = options.get("awslogs-stream-prefix", "")
        if not log_group:
            continue

        sources.append({
            "containerName": container.get("name", ""),
            "logDriver": log_cfg.get("logDriver", ""),
            "logGroup": log_group,
            "logStreamPrefix": stream_prefix,
            "awslogsRegion": options.get("awslogs-region", AWS_REGION),
        })
    return sources


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@mcp.tool()
def list_ecs_clusters() -> str:
    """List all ECS clusters in the AWS account."""
    ecs = get_boto3_client("ecs")
    arns = _paginate(ecs, "list_clusters", "clusterArns")
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
    return _json(result)


@mcp.tool()
def list_ecs_services(cluster_name: str) -> str:
    """List all ECS services in a given ECS cluster."""
    ecs = get_boto3_client("ecs")
    arns = _paginate(ecs, "list_services", "serviceArns", cluster=cluster_name)
    if not arns:
        return f"No services found in cluster: {cluster_name}"

    services = ecs.describe_services(cluster=cluster_name, services=arns)["services"]
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
    return _json(result)


@mcp.tool()
def get_ecs_service_details(cluster_name: str, service_name: str) -> str:
    """
    Get full details of a specific ECS service including task counts,
    deployments, and events.
    """
    ecs = get_boto3_client("ecs")
    s = _get_service(ecs, cluster_name, service_name)
    if not s:
        return f"Service '{service_name}' not found in cluster '{cluster_name}'"

    result = {
        "serviceName": s["serviceName"],
        "status": s["status"],
        "desiredCount": s["desiredCount"],
        "runningCount": s["runningCount"],
        "pendingCount": s["pendingCount"],
        "launchType": s.get("launchType", "N/A"),
        "taskDefinition": s.get("taskDefinition", ""),
        "capacityProviderStrategy": s.get("capacityProviderStrategy", []),
        "loadBalancers": s.get("loadBalancers", []),
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
    return _json(result)


@mcp.tool()
def get_cluster_capacity_summary(cluster_name: str) -> str:
    """
    Return ECS cluster task counts plus the backing ASG instance counts.
    Use this when the user asks for 'instance counts' for a cluster.
    """
    ecs = get_boto3_client("ecs")
    cluster = _get_cluster(ecs, cluster_name)
    if not cluster:
        return f"Cluster '{cluster_name}' not found."

    asg_backing = _resolve_asg_backing_cluster(cluster_name)

    result = {
        "clusterName": cluster["clusterName"],
        "status": cluster["status"],
        "activeServicesCount": cluster["activeServicesCount"],
        "runningTasksCount": cluster["runningTasksCount"],
        "pendingTasksCount": cluster["pendingTasksCount"],
        "backingAsgs": asg_backing,
        "totalBackingInstanceCount": sum(x["instanceCount"] for x in asg_backing),
        "totalBackingInstancesInService": sum(x["instancesInService"] for x in asg_backing),
    }
    return _json(result)


@mcp.tool()
def update_ecs_service_desired_count(cluster_name: str, service_name: str, desired_count: int) -> str:
    """
    Update the desired count of an ECS service. This changes live infrastructure.
    Use only when the user explicitly asks to scale a service up or down.
    """
    if desired_count < 0:
        return "desired_count must be >= 0"

    ecs = get_boto3_client("ecs")

    before = _get_service(ecs, cluster_name, service_name)
    if not before:
        return f"Service '{service_name}' not found in cluster '{cluster_name}'"

    response = ecs.update_service(
        cluster=cluster_name,
        service=service_name,
        desiredCount=desired_count,
    )

    after = response["service"]

    result = {
        "clusterName": cluster_name,
        "serviceName": after["serviceName"],
        "status": after["status"],
        "previousDesiredCount": before["desiredCount"],
        "newDesiredCount": after["desiredCount"],
        "runningCount": after["runningCount"],
        "pendingCount": after["pendingCount"],
        "taskDefinition": after.get("taskDefinition", ""),
        "deployments": [
            {
                "status": d["status"],
                "desiredCount": d["desiredCount"],
                "runningCount": d["runningCount"],
                "pendingCount": d["pendingCount"],
                "createdAt": str(d["createdAt"]),
                "updatedAt": str(d["updatedAt"]),
            }
            for d in after.get("deployments", [])
        ],
    }
    return _json(result)


@mcp.tool()
def get_ecs_service_recent_events(cluster_name: str, service_name: str, limit: int = 30) -> str:
    """
    Return recent ECS service events and identify the most recent deregistration-like event,
    if any, based on ECS event messages.
    """
    ecs = get_boto3_client("ecs")
    s = _get_service(ecs, cluster_name, service_name)
    if not s:
        return f"Service '{service_name}' not found in cluster '{cluster_name}'"

    events = s.get("events", [])[:max(1, min(limit, 100))]
    formatted_events = [
        {
            "createdAt": str(e["createdAt"]),
            "message": e["message"],
        }
        for e in events
    ]

    keywords = ("deregister", "deregistration", "draining", "drain")
    last_dereg = None
    for e in formatted_events:
        if any(k in e["message"].lower() for k in keywords):
            last_dereg = e
            break

    result = {
        "clusterName": cluster_name,
        "serviceName": s["serviceName"],
        "taskDefinition": s.get("taskDefinition", ""),
        "loadBalancers": s.get("loadBalancers", []),
        "lastDeregistrationLikeEvent": last_dereg,
        "recentEvents": formatted_events,
    }
    return _json(result)


@mcp.tool()
def get_cloudwatch_logs_for_ecs_service(
    cluster_name: str,
    service_name: str,
    minutes_back: int = 60,
    limit: int = 100,
    filter_pattern: str = "",
    around_time: str = "",
    minutes_before: int = 15,
    minutes_after: int = 15,
) -> str:
    """
    Fetch CloudWatch logs for the ECS service by resolving awslogs configuration
    from the service's task definition.

    If around_time is provided (ISO-8601 string), logs are fetched in a window
    around that timestamp. Otherwise logs are fetched for the last minutes_back minutes.
    """
    ecs = get_boto3_client("ecs")
    logs = get_boto3_client("logs")

    service = _get_service(ecs, cluster_name, service_name)
    if not service:
        return f"Service '{service_name}' not found in cluster '{cluster_name}'"

    task_definition_arn = service.get("taskDefinition")
    if not task_definition_arn:
        return f"Service '{service_name}' does not have a task definition."

    td = ecs.describe_task_definition(taskDefinition=task_definition_arn)["taskDefinition"]
    log_sources = _extract_log_sources_from_task_definition(td)
    if not log_sources:
        return (
            f"No awslogs configuration found for service '{service_name}'. "
            f"CloudWatch log fetching is not available for this service."
        )

    now = datetime.now(timezone.utc)
    if around_time:
        center = _parse_time(around_time)
        start_dt = center - timedelta(minutes=minutes_before)
        end_dt = center + timedelta(minutes=minutes_after)
    else:
        end_dt = now
        start_dt = now - timedelta(minutes=minutes_back)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    per_query_limit = max(1, min(limit, 1000))

    collected = []
    for source in log_sources:
        kwargs = {
            "logGroupName": source["logGroup"],
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": per_query_limit,
        }

        if source["logStreamPrefix"]:
            kwargs["logStreamNamePrefix"] = source["logStreamPrefix"]
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern

        next_token = None
        while True:
            if next_token:
                kwargs["nextToken"] = next_token
            else:
                kwargs.pop("nextToken", None)

            resp = logs.filter_log_events(**kwargs)

            for event in resp.get("events", []):
                collected.append({
                    "timestamp": datetime.fromtimestamp(
                        event["timestamp"] / 1000, tz=timezone.utc
                    ).isoformat(),
                    "logGroup": source["logGroup"],
                    "logStream": event.get("logStreamName", ""),
                    "message": event.get("message", "").rstrip(),
                })

            next_token = resp.get("nextToken")
            if not next_token:
                break
            if len(collected) >= limit:
                break

        if len(collected) >= limit:
            break

    collected.sort(key=lambda x: x["timestamp"])
    collected = collected[:limit]

    result = {
        "clusterName": cluster_name,
        "serviceName": service_name,
        "taskDefinition": task_definition_arn,
        "logSources": log_sources,
        "queryWindowStart": start_dt.isoformat(),
        "queryWindowEnd": end_dt.isoformat(),
        "events": collected,
    }
    return _json(result)


@mcp.tool()
def get_asg_for_ecs_cluster(cluster_name: str) -> str:
    """
    Find the Auto Scaling Group linked to an ECS cluster by resolving
    the capacity provider -> ASG ARN chain. Works for EC2 launch type
    with ASG capacity providers.
    """
    results = _resolve_asg_backing_cluster(cluster_name)
    if not results:
        return "No ASG-backed capacity providers found."
    return _json(results)


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
    return _json(activities)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
