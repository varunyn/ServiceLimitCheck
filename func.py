import oci
import datetime
import json
import logging
import io
import concurrent.futures


from fdk import response

# Set the default usage threshold percentage
DEFAULT_THRESHOLD_PERCENTAGE = 90

# Set the default policy limit
DEFAULT_POLICY_LIMIT = 100


# Initialize summary buffer for log entries
summary_buffer = []
error_buffer = []
logged_entries = set()  # Set to track logged entries and avoid duplicates

# Set up logger
logging.basicConfig(
    filename="app_timeout.log", 
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s : %(message)s"
)

# Function to log debug messages (for step-by-step tracking)
def log_debug_message(message):
    logging.getLogger().debug(message)

# Function to log messages to both console and buffer (to be sent via email)
def log_message(message):
    logging.getLogger().info(message)
    summary_buffer.append(message)

# Function to add errors to the error log buffer (to be optionally sent via email)
def add_to_error_log(message):
    error_buffer.append(message)

# Function to send the summary via OCI Notifications
def send_notification(notification_topic_id, signer):
    summary_message = "\n".join(summary_buffer)
    error_message = "\n".join(error_buffer)

    # Check if the summary message is empty
    if not summary_message:
        summary_message = "No resources are near their usage limits."

    notification_message = f"OCI Resource Usage Report - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    
    # Append errors to the notification only if there are errors
    if error_message:
        notification_message += f"Errors encountered:\n{error_message}\n\n"

    # Append the resource usage summary
    notification_message += summary_message

    notification_client = oci.ons.NotificationDataPlaneClient({}, signer=signer)
    try:
        response = notification_client.publish_message(
            topic_id=notification_topic_id,
            message_details=oci.ons.models.MessageDetails(
                title="OCI Resource Usage Alert",
                body=notification_message
            )
        )
        #log_message(f"Notification sent successfully: {response.data}")
    except oci.exceptions.ServiceError as e:
        # Log and capture the error in the error log
        error_message = f"Failed to send notification: {str(e)}"
        log_message(error_message)
        add_to_error_log(error_message)

# Function to list all available services
def list_all_services(compartment_id, limits_client, limit=1000):
    services = []
    next_page = None
    while True:
        services_response = limits_client.list_services(
            compartment_id=compartment_id,
            page=next_page,
            limit=limit
        )
        services.extend(services_response.data)
        
        next_page = services_response.headers.get('opc-next-page')
        if not next_page:
            break
    return services

# Function to list all availability domains in the tenancy
def list_availability_domains(identity_client, compartment_id):
    availability_domains = []
    ads_response = identity_client.list_availability_domains(compartment_id=compartment_id)
    availability_domains.extend([ad.name for ad in ads_response.data])
    return availability_domains

# Function to get all limit definitions for a specific service
def get_all_limit_definitions(service_name, compartment_id, limits_client, limit=1000):
    limit_definitions = []
    next_page = None
    while True:
        limit_definitions_response = limits_client.list_limit_definitions(
            compartment_id=compartment_id,
            service_name=service_name,
            page=next_page,
            limit=limit
        )

        limit_definitions.extend(limit_definitions_response.data)
        next_page = limit_definitions_response.headers.get('opc-next-page')
        if not next_page:
            break
    return limit_definitions

# Function to get all limit values for a service
def get_all_limit_values(service_name, compartment_id, limits_client, limit=1000):
    limit_values = []
    next_page = None
    while True:
        limit_values_response = limits_client.list_limit_values(
            compartment_id=compartment_id,
            service_name=service_name,
            page=next_page,
            limit=limit
        )

        limit_values.extend(limit_values_response.data)
        next_page = limit_values_response.headers.get('opc-next-page')
        if not next_page:
            break
    return limit_values

# Example of adding debug logs to a resource check function
def get_resource_availability(service_name, limit_name, compartment_id, limits_client, availability_domain=None, scope_type=None):
    log_debug_message(f"Fetching resource availability for {service_name} - {limit_name} in {availability_domain or 'Region'}")
    try:
        if scope_type == 'AD':
            availability_data = limits_client.get_resource_availability(
                compartment_id=compartment_id,
                service_name=service_name,
                limit_name=limit_name,
                availability_domain=availability_domain
            ).data
        else:
            availability_data = limits_client.get_resource_availability(
                compartment_id=compartment_id,
                service_name=service_name,
                limit_name=limit_name
            ).data

        log_debug_message(f"Resource availability fetched for {service_name} - {limit_name}")
        return availability_data.used, availability_data.available
    except oci.exceptions.ServiceError as e:
        error_message = f"Error fetching resource availability: {str(e)}"
        log_message(error_message)
        log_debug_message(error_message)
        return None, None

# Function to log usage if above threshold into a summary buffer
def log_usage_if_above_threshold(service_name, scope_type, availability_domain, limit_name, service_limit, usage, available, threshold_percentage):
    if service_limit > 0:  # To avoid division by zero
        usage_percentage = (usage / service_limit) * 100
        entry_key = (service_name, scope_type, availability_domain, limit_name)
        
        # Avoid duplicate entries by checking if the entry already exists in the set
        if entry_key not in logged_entries and usage_percentage >= threshold_percentage:
            # Log the resource if usage exceeds the threshold
            log_entry = (f"Service: {service_name}, Scope: {scope_type}, AD: {availability_domain or 'N/A'}, "
                         f"Limit Name: {limit_name}, Limit: {service_limit}, Usage: {usage}, Available: {available}, "
                         f"Usage %: {usage_percentage:.2f}%")
            
            log_message(log_entry)  # Add to the summary buffer
            logged_entries.add(entry_key)  # Add the entry to the logged entries set

# Function to count policies in the tenancy
def count_policies(identity_client, compartment_id):
    policies = []
    next_page = None
    
    while True:
        response = identity_client.list_policies(compartment_id=compartment_id, page=next_page)
        policies.extend(response.data)
        
        next_page = response.headers.get('opc-next-page')
        if not next_page:
            break

    return len(policies)

# Function to check policy limits
def check_policy_limits(identity_client, policy_limit, compartment_id):
    policy_count = count_policies(identity_client, compartment_id)
    log_message(f"Total number of policies in the tenancy: {policy_count}")
    policy_limit = float(policy_limit)

    if policy_limit > 0:
        policy_usage = (policy_count / policy_limit) * 100
        if policy_usage >= DEFAULT_THRESHOLD_PERCENTAGE:
            log_message(f"Policy usage is at {policy_usage:.2f}% of the allowed limit ({policy_count}/{policy_limit})\n")
        else:
            log_message(f"Policy usage is below threshold: {policy_usage:.2f}%\n")

# Function to find the tenancy's home region
def get_home_region(identity_client, tenancy_id):
    regions = identity_client.list_region_subscriptions(tenancy_id).data
    for region in regions:
        if region.is_home_region:
            return region.region_name
    return None

def process_service(service, compartment_id, limits_client, threshold_percentage, availability_domains):
    service_name = service.name
    limit_definitions = get_all_limit_definitions(service_name, compartment_id, limits_client)
    limit_values = get_all_limit_values(service_name, compartment_id, limits_client)

    for definition in limit_definitions:
        if definition.is_deprecated:
            continue

        limit_name = definition.name
        scope_type = definition.scope_type
        service_limit = next((lv.value for lv in limit_values if lv.name == limit_name), None)

        if service_limit is None:
            continue

        if scope_type == 'AD':
            for ad in availability_domains:
                usage, available = get_resource_availability(
                    service_name, limit_name, compartment_id, limits_client, ad, scope_type='AD'
                )
                if usage is not None and available is not None:
                    log_usage_if_above_threshold(service_name, scope_type, ad, limit_name, service_limit, usage, available, threshold_percentage)
        else:
            usage, available = get_resource_availability(
                service_name, limit_name, compartment_id, limits_client, scope_type=scope_type
            )
            if usage is not None and available is not None:
                log_usage_if_above_threshold(service_name, scope_type, None, limit_name, service_limit, usage, available, threshold_percentage)

def check_service_limits(signer, notification_topic_id, regions, threshold_percentage, policy_limit=None):
    try:
        identity_client = oci.identity.IdentityClient({},signer=signer)
        tenancy_id = signer.tenancy_id
        compartment_id = tenancy_id

        if policy_limit is not None:
            check_policy_limits(identity_client, policy_limit, compartment_id)

        if regions == 'all':
            regions = identity_client.list_region_subscriptions(tenancy_id).data
            region_names = [r.region_name for r in regions]
        elif isinstance(regions, list):
            region_names = regions
        else:
            region_names = [regions]

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            for region_name in region_names:
                log_message(f"Processing region: {region_name}")
                config = {"region": region_name}
                limits_client = oci.limits.LimitsClient(config,signer=signer)

                services = list_all_services(compartment_id, limits_client)
                availability_domains = list_availability_domains(identity_client, compartment_id)

                # Use executor to process services in parallel
                executor.map(lambda service: process_service(service, compartment_id, limits_client,
                                                              threshold_percentage,
                                                              availability_domains), services)

        send_notification(notification_topic_id, signer)
        return {"message": "Function executed successfully."}

    except Exception as ex:
        error_message = f"Function execution failed: {str(ex)}"
        log_message(error_message)
        add_to_error_log(error_message)
        return {"error": error_message}

def handler(ctx, data: io.BytesIO = None):
    try:
        log_debug_message("Handler function started")
        config_params = ctx.Config()
        notification_topic_id = config_params.get("notification_topic_id")
        regions_string = config_params.get("regions")
        policy_limit = config_params.get("policy_limit",DEFAULT_POLICY_LIMIT)
        
        if regions_string:
            regions = json.loads(regions_string)
        else:
            regions = []

        log_debug_message(f"Regions: {regions}")
        threshold_percentage = config_params.get("threshold_percentage", DEFAULT_THRESHOLD_PERCENTAGE)
        
        if not notification_topic_id:
            log_message("Error: Notification topic ID not provided.")
            return response.Response(
                ctx, response_data=json.dumps({"error": "Notification topic ID not provided"}),
                headers={"Content-Type": "application/json"}
            )

        signer = oci.auth.signers.get_resource_principals_signer()
        if not regions:
            identity_client = oci.identity.IdentityClient({}, signer=signer)
            home_region = get_home_region(identity_client, signer.tenancy_id)
            regions = [home_region]
            log_message(f"Defaulting to home region: {regions}")
        
        log_debug_message("Starting service limit checks")
        resp = check_service_limits(signer, notification_topic_id, regions, float(threshold_percentage),policy_limit)

        log_debug_message("Service limit checks completed")
        return response.Response(
            ctx, response_data=json.dumps(resp),
            headers={"Content-Type": "application/json"}
        )
    
    except Exception as ex:
        error_message = f"Function execution failed: {str(ex)}"
        log_message(error_message)
        return response.Response(
            ctx, response_data=json.dumps({"error": error_message}),
            headers={"Content-Type": "application/json"}
        )
