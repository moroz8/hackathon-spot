import argparse
import os
import sys
import time

import bosdyn.client
import bosdyn.client.util
from bosdyn.choreography.client.choreography import (ChoreographyClient,
                                                     load_choreography_sequence_from_txt_file)
from bosdyn.client import ResponseError, RpcError, create_standard_sdk
from bosdyn.client.exceptions import UnauthenticatedError
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive
from bosdyn.client.license import LicenseClient

ROBOT_IP = "10.0.0.3"#os.environ['ROBOT_IP']
SPOT_USERNAME = "admin"#os.environ['SPOT_USERNAME']
SPOT_PASSWORD = "2zqa8dgw7lor"#os.environ['SPOT_PASSWORD']

BUCKET_TOCKEN = "eyJpZCI6IDEsICJ2YWwiOiAiMUtfMmdGeUdHaVBMVGJGVlZKbUZTS25uRVh0RDBoVlNkQW5pTE5kR213VEkxUXdZSXBNekhLQlFBSG9zMmF0UzU5dExLeEVBZmRBNFpHbWNyeTRvTlEifQ=="

def main():
    username=SPOT_USERNAME, password=SPOT_PASSWORD, robot_ip=ROBOT_IP

    # Parse args
    parser = argparse.ArgumentParser()
    bosdyn.client.util.add_base_arguments(parser)
    parser.add_argument('--choreography-filepath',
                        help='The filepath to load the choreographed sequence text file from.')
    parser.add_argument('--upload-only', action='store_true',
                        help='Only upload, without executing.')
    options = parser.parse_args()

    # Create robot object with the ability to access the ChoreographyClient
    sdk = bosdyn.client.create_standard_sdk('UploadChoreography')
    sdk.register_service_client(ChoreographyClient)
    robot = sdk.create_robot(ROBOT_IP)
    robot.authenticate(SPOT_USERNAME, SPOT_PASSWORD) 
    bosdyn.client.util.authenticate(robot)

    license_client = robot.ensure_client(LicenseClient.default_service_name)
    if not license_client.get_feature_enabled([ChoreographyClient.license_name
                                              ])[ChoreographyClient.license_name]:
        print('This robot is not licensed for choreography.')
        sys.exit(1)

    # Check that an estop is connected with the robot so that the robot commands can be executed.
    assert not robot.is_estopped(), 'Robot is estopped. Please use an external E-Stop client, ' \
                                    'such as the estop SDK example, to configure E-Stop.'

    # Create a lease and lease keep-alive so we can issue commands. A lease is required to execute
    # a choreographed sequence.
    lease_client = robot.ensure_client(LeaseClient.default_service_name)
    lease = lease_client.acquire()
    lk = LeaseKeepAlive(lease_client)

    # Create the client for the Choreography service.
    choreography_client = robot.ensure_client(ChoreographyClient.default_service_name)

    # Load the choreography from a text file into a local protobuf ChoreographySequence message.
    if options.choreography_filepath:
        # Use the filepath provided.
        try:
            choreography = load_choreography_sequence_from_txt_file(options.choreography_filepath)
        except Exception as excep:
            print(f'Failed to load choreography. Raised exception: {excep}')
            return True
    else:
        # Use a default dance stored in this directory.
        default_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_DANCE)
        try:
            choreography = load_choreography_sequence_from_txt_file(default_filepath)
        except Exception as excep:
            print(f'Failed to load choreography. Raised exception: {excep}')
            return True

    # Once the choreography is loaded into a protobuf message, upload the routine to the robot. We set
    # non_strict_parsing to true so that the robot will automatically correct any errors it finds in the routine.
    try:
        upload_response = choreography_client.upload_choreography(choreography,
                                                                  non_strict_parsing=True)
    except UnauthenticatedError as err:
        print(
            'The robot license must contain \'choreography\' permissions to upload and execute dances. '
            'Please contact Boston Dynamics Support to get the appropriate license file. ')
        return True
    except ResponseError as err:
        # Check if the ChoreographyService considers the uploaded routine as valid. If not, then the warnings must be
        # addressed before the routine can be executed on robot.
        error_msg = 'Choreography sequence upload failed. The following warnings were produced: '
        for warn in err.response.warnings:
            error_msg += warn
        print(error_msg)
        return True

    sequences_on_robot = choreography_client.list_all_sequences()
    known_sequences = '\n'.join(sequences_on_robot.known_sequences)
    print(f'Sequence uploaded. All sequences on the robot:\n{known_sequences}')
    if options.upload_only:
        return True

    # If the routine was valid, then we can now execute the routine on robot.
    # Power on the robot. The robot can start from any position, since the Choreography Service can automatically
    # figure out and move the robot to the position necessary for the first move.
    robot.power_on()

    # First, get the name of the choreographed sequence that was uploaded to the robot to uniquely identify which
    # routine to perform.
    routine_name = choreography.name
    # Then, set a start time five seconds after the current time.
    delayed_start = 5.0
    client_start_time = time.time() + delayed_start
    # Specify the starting slice of the choreography. We will set this to slice=0 so that the routine begins at
    # the very beginning.
    start_slice = 0
    # Issue the command to the robot's choreography service.
    choreography_client.execute_choreography(choreography_name=routine_name,
                                             client_start_time=client_start_time,
                                             choreography_starting_slice=start_slice)

    # Estimate how long the choreographed sequence will take.
    total_choreography_slices = 0
    for move in choreography.moves:
        # Calculate the slice when the move will end, by adding the duration of the
        # move(requested_slices) to the slice when the move will start(start_slice).
        end_slice = move.start_slice + move.requested_slices

        # Store the highest end_slice value of all the moves. This is the last active slice of the dance.
        if total_choreography_slices < end_slice:
            total_choreography_slices = end_slice

    estimated_time_seconds = delayed_start + total_choreography_slices / choreography.slices_per_minute * 60.0

    # Sleep for the duration of the dance, plus an extra second.
    time.sleep(estimated_time_seconds + 1.0)

    # Sit the robot down and power off the robot.
    robot.power_off()
    return True


if __name__ == '__main__':
    main()
